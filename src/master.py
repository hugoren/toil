#!/usr/bin/env python

#Copyright (C) 2011 by Benedict Paten (benedictpaten@gmail.com)
#
#Permission is hereby granted, free of charge, to any person obtaining a copy
#of this software and associated documentation files (the "Software"), to deal
#in the Software without restriction, including without limitation the rights
#to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
#copies of the Software, and to permit persons to whom the Software is
#furnished to do so, subject to the following conditions:
#
#The above copyright notice and this permission notice shall be included in
#all copies or substantial portions of the Software.
#
#THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
#IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
#FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
#AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
#LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
#OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
#THE SOFTWARE.

"""The master component (of a master slave pattern) for a job manager used by
sontrace programs (cactus etc) for running hierarchical trees of jobs on the
cluster.

Takes a crash-only philosophy so that any part of the process can be failed
and then restarted at will (see the accompanying tests).
"""

import os
import sys
import re
import os.path
import xml.etree.cElementTree as ET
import time
import shutil
import socket
import random
from collections import deque
#from threading import Thread, Queue
from multiprocessing import Process, Queue

from job import Job, JobDB, JobTreeState
from sonLib.bioio import logger, getTotalCpuTime
from sonLib.bioio import system
from jobTree.src.bioio import workflowRootPath
from sonLib.bioio import TempFileTree

####
#Little functions to specify the location of files in the jobTree dir
####

def getEnvironmentFileName(jobTreePath):
    return os.path.join(jobTreePath, "environ.pickle")

def getStatsFileName(jobTreePath):
    return os.path.join(jobTreePath, "stats.xml")

def getStatsCacheFileName(jobTreePath):
    return os.path.join(jobTreePath, ".stats_cache.pickle")

def getParasolResultsFileName(jobTreePath):
    return os.path.join(jobTreePath, "results.txt")

def getConfigFileName(jobTreePath):
    return os.path.join(jobTreePath, "config.xml")

#####
##The following functions are used for collating stats from the slaves
####

def getTempStatDirNames():
    return [ "1", "2", "3", "4", "5", "6", "7", "8", "9", "10"]

def getTempStatsFile(jobTreePath):
    return os.path.join(jobTreePath, "stats", random.choice(getTempStatDirNames()), random.choice(getTempStatDirNames()), "%s_%s.xml" % (socket.gethostname(), os.getpid()))

def makeTemporaryStatsDirs(jobTreePath):
    #Temp dirs
    def fn(dir, subDir):
        absSubDir = os.path.join(dir, subDir)
        if not os.path.exists(absSubDir):
            os.mkdir(absSubDir)
        return absSubDir
    statsDir = fn(jobTreePath, "stats")
    return reduce(lambda x,y: x+y, [ [ fn(absSubDir, subSubDir) for subSubDir in getTempStatDirNames() ] for absSubDir in [ fn(statsDir, subDir) for subDir in getTempStatDirNames() ] ], [])

def statsAggregatorProcess(jobTreePath, tempDirs, stop):
    #Overall timing
    startTime = time.time()
    startClock = getTotalCpuTime()

    #Start off the stats file
    fileHandle = open(getStatsFileName(jobTreePath), 'w')
    fileHandle.write('<?xml version="1.0" ?><stats>')
    statsFile = getStatsFileName(jobTreePath)

    #The main loop
    timeSinceOutFileLastFlushed = time.time()
    while True:
        def scanDirectoriesAndScrapeStats():
            numberOfFilesProcessed = 0
            for dir in tempDirs:
                for tempFile in os.listdir(dir):
                    if tempFile[-3:] != "new":
                        absTempFile = os.path.join(dir, tempFile)
                        fH = open(absTempFile, 'r')
                        for line in fH.readlines():
                            fileHandle.write(line)
                        fH.close()
                        os.remove(absTempFile)
                        numberOfFilesProcessed += 1
            return numberOfFilesProcessed 
        if not stop.empty(): #This is a indirect way of getting a message to the process to exit
            scanDirectoriesAndScrapeStats()
            break
        if scanDirectoriesAndScrapeStats() == 0:
            time.sleep(0.5) #Avoid cycling too fast
        if time.time() - timeSinceOutFileLastFlushed > 60: #Flush the results file every minute
            fileHandle.flush() 
            timeSinceOutFileLastFlushed = time.time()

    #Finish the stats file
    fileHandle.write("<total_time time='%s' clock='%s'/></stats>" % (str(time.time() - startTime), str(getTotalCpuTime() - startClock)))
    fileHandle.close()

#####
##Following encapsulates interations with batch system class.
####

class JobBatcher:
    """Class works with jobBatcherWorker to submit jobs to the batch system.
    """
    def __init__(self, config, batchSystem, jobDB):
        self.config = config
        self.jobDB = jobDB
        self.jobTree = config.attrib["job_tree"]
        self.jobIDsToJobsHash = {}
        self.batchSystem = batchSystem
        self.jobsIssued = 0
        self.jobTreeSlavePath = os.path.join(workflowRootPath(), "src", "jobTreeSlave.py")
        self.rootPath = os.path.split(workflowRootPath())[0]
        self.reissueMissingJobs_missingHash = {} #Hash to store number of observed misses

    def issueJob(self, jobFile, memory, cpu):
        """Add a job to the queue of jobs
        """
        self.jobsIssued += 1
        jobCommand = "%s -E %s %s %s %s" % (sys.executable, self.jobTreeSlavePath, self.rootPath, self.jobTree, jobFile)
        jobID = self.batchSystem.issueJob(jobCommand, memory, cpu)
        self.jobIDsToJobsHash[jobID] = jobFile
        logger.debug("Issued the job: %s with job id: %s and cpus: %i" % (jobFile, str(jobID), cpu))

    def issueJobs(self, jobs):
        """Add a list of jobs
        """
        for jobFile, memory, cpu in jobs:
            self.issueJob(jobFile, memory, cpu)

    def getNumberOfJobsIssued(self):
        """Gets number of jobs that have been added by issueJob(s) and not removed by removeJobID
        """
        assert self.jobsIssued >= 0
        return self.jobsIssued

    def getJob(self, jobID):
        """Gets the job file associated the a given id
        """
        return self.jobIDsToJobsHash[jobID]

    def hasJob(self, jobID):
        """Returns true if the jobID is in the list of jobs.
        """
        return self.jobIDsToJobsHash.has_key(jobID)

    def getJobIDs(self):
        """Gets the set of jobs currently issued.
        """
        return self.jobIDsToJobsHash.keys()

    def removeJobID(self, jobID):
        """Removes a job from the jobBatcher.
        """
        assert jobID in self.jobIDsToJobsHash
        self.jobsIssued -= 1
        jobFile = self.jobIDsToJobsHash.pop(jobID)
        return jobFile
    
    def killJobs(self, jobsToKill):
        """Kills the given set of jobs and then sends them for processing
        """
        if len(jobsToKill) > 0:
            self.batchSystem.killJobs(jobsToKill)
            for jobID in jobsToKill:
                self.jobDB.processFinishedJob(self.removeJobID(jobID), 1)
    
    #Following functions handle error cases for when jobs have gone awry with the batch system.
            
    def reissueOverLongJobs(self):
        """Check each issued job - if it is running for longer than desirable.. issue a kill instruction.
        Wait for the job to die then we pass the job to processFinishedJob.
        """
        maxJobDuration = float(self.config.attrib["max_job_duration"])
        idealJobTime = float(self.config.attrib["job_time"])
        if maxJobDuration < idealJobTime * 10:
            logger.info("The max job duration is less than 10 times the ideal the job time, so I'm setting it to the ideal job time, sorry, but I don't want to crash your jobs because of limitations in jobTree ")
            maxJobDuration = idealJobTime * 10
        jobsToKill = []
        if maxJobDuration < 10000000: #We won't both doing anything is the rescue time is more than 16 weeks.
            runningJobs = self.batchSystem.getRunningJobIDs()
            for jobID in runningJobs.keys():
                if runningJobs[jobID] > maxJobDuration:
                    logger.critical("The job: %s has been running for: %s seconds, more than the max job duration: %s, we'll kill it" % \
                                (str(self.getJob(jobID)), str(runningJobs[jobID]), str(maxJobDuration)))
                    jobsToKill.append(jobID)
            self.killJobs(jobsToKill)
    
    def reissueMissingJobs(self, killAfterNTimesMissing=3):
        """Check all the current job ids are in the list of currently running batch system jobs.
        If a job is missing, we mark it as so, if it is missing for a number of runs of
        this function (say 10).. then we try deleting the job (though its probably lost), we wait
        then we pass the job to processFinishedJob.
        """
        runningJobs = set(self.batchSystem.getIssuedJobIDs())
        jobIDsSet = set(self.getJobIDs())
        #Clean up the reissueMissingJobs_missingHash hash, getting rid of jobs that have turned up
        missingJobIDsSet = set(reissueMissingJobs_missingHash.keys())
        for jobID in missingJobIDsSet.difference(jobIDsSet):
            reissueMissingJobs_missingHash.pop(jobID)
            logger.critical("Job id %s is no longer missing" % str(jobID))
        assert runningJobs.issubset(jobIDsSet) #Assert checks we have no unexpected jobs running
        jobsToKill = []
        for jobID in set(jobIDsSet.difference(runningJobs)):
            jobFile = self.getJob(jobID)
            if reissueMissingJobs_missingHash.has_key(jobID):
                reissueMissingJobs_missingHash[jobID] = reissueMissingJobs_missingHash[jobID]+1
            else:
                reissueMissingJobs_missingHash[jobID] = 1
            timesMissing = reissueMissingJobs_missingHash[jobID]
            logger.critical("Job %s with id %s is missing for the %i time" % (jobFile, str(jobID), timesMissing))
            if timesMissing == killAfterNTimesMissing:
                reissueMissingJobs_missingHash.pop(jobID)
                jobsToKill.append(jobID)
        self.killJobs(jobsToKill)
        return len(reissueMissingJobs_missingHash) == 0 #We use this to inform if there are missing jobs

####
#The main loop
####

def mainLoop(config, batchSystem):
    """This is the main loop from which jobs are issued and processed.
    """
    rescueJobsFrequency = float(config.attrib["rescue_jobs_frequency"])
    maxJobDuration = float(config.attrib["max_job_duration"])
    assert maxJobDuration >= 0
    logger.info("Got parameters,rescue jobs frequency: %s max job duration: %s" % \
                (rescueJobsFrequency, maxJobDuration))

    #Kill any jobs on the batch system queue from the last time.
    assert len(batchSystem.getIssuedJobIDs()) == 0 #Batch system must start with no active jobs!
    logger.info("Checked batch system has no running jobs and no updated jobs")

    jobDB = JobDB(config)
    jobDB.loadJobTreeState() #This initialises the object jobTree.jobTreeState used to track the active jobTree
    jobBatcher = JobBatcher(config, batchSystem, jobDB)
    logger.info("Found %s jobs to start and %i parent jobs with children to run" % (len(jobDB.jobTreeState.updatedJobs), len(jobDB.jobTreeState.childCounts)))

    stats = config.attrib.has_key("stats")
    if stats:
        stop = Queue()
        worker = Process(target=statsAggregatorProcess, args=(config.attrib["job_tree"], makeTemporaryStatsDirs(config.attrib["job_tree"]), stop))
        worker.daemon = True
        worker.start()

    timeSinceJobsLastRescued = time.time() #Sets up the timing of the job rescuing method
    totalFailedJobs = 0
    logger.info("Starting the main loop")
    while True:
        if len(jobDB.jobTreeState.updatedJobs) > 0:
            logger.debug("Built the jobs list, currently have %i jobs to update and %i jobs issued" % (len(jobDB.jobTreeState.updatedJobs), jobBatcher.getNumberOfJobsIssued()))

            for job in jobDB.jobTreeState.updatedJobs:
                for message in job.messages:
                    logger.critical("Got message from job at time: %s : %s" % (time.strftime("%m-%d-%Y %H:%M:%S"), message))
                job.messages = []

                if len(job.children) > 0:
                    logger.debug("Job: %s has %i children to schedule" % (job.jobStoreID, len(job.children)))
                    children = job.children
                    job.children = []
                    for childJobStoreID, memory, cpu in children:
                        jobDB.jobTreeState.childJobStoreIdToParentJob[childJobStoreID] = job
                    assert job not in jobDB.jobTreeState.childCounts
                    jobDB.jobTreeState.childCounts[job] = len(children)
                    jobBatcher.issueJobs(children)
                else:
                    assert len(job.followOnCommands) > 0
                    if job.remainingRetryCount > 0:
                        logger.debug("Job: %s has a new command that we can now issue" % job.jobStoreID)
                        memory, cpu = job.followOnCommands[-1][1:3]
                        jobBatcher.issueJob(job.jobStoreID, memory, cpu)
                    else:
                        totalFailedJobs += 1
                        logger.critical("Job: %s is completely failed" % job.jobStoreID)
            jobDB.jobTreeState.updatedJobs = set() #We've considered them all, so reset

        if jobBatcher.getNumberOfJobsIssued() == 0:
            logger.info("Only failed jobs and their dependents (%i total) are remaining, so exiting." % totalFailedJobs)
            break

        updatedJob = batchSystem.getUpdatedJob(10) #Asks the batch system what jobs have been completed.
        if updatedJob != None:
            jobID, result = updatedJob
            if jobBatcher.hasJob(jobID):
                if result == 0:
                    logger.debug("Batch system is reporting that the job %s ended successfully" % jobBatcher.getJob(jobID))
                else:
                    logger.critical("Batch system is reporting that the job %s %s failed with exit value %i" % (jobID, jobBatcher.getJob(jobID), result))
                jobDB.processFinishedJob(jobBatcher.removeJobID(jobID), result)
            else:
                logger.critical("A result seems to already have been processed: %i" % jobID)
        else:
            #logger.debug("Waited but no job was finished, still have %i jobs issued" % jobBatcher.getNumberOfJobsIssued())
            if time.time() - timeSinceJobsLastRescued >= rescueJobsFrequency: #We only rescue jobs every N seconds, and when we have apparently exhausted the current job supply
                jobBatcher.reissueOverLongJobs()
                logger.info("Reissued any over long jobs")

                hasNoMissingJobs = jobBatcher.reissueMissingJobs()
                if hasNoMissingJobs:
                    timeSinceJobsLastRescued = time.time()
                else:
                    timeSinceJobsLastRescued += 60 #This means we'll try again in a minute, providing things are quiet
                logger.info("Rescued any (long) missing jobs")

    logger.info("Finished the main loop")

    if stats:
        startTime = time.time()
        logger.info("Waiting for stats collator process to finish")
        stop.put(True)
        worker.join()
        logger.info("Stats finished collating in %s seconds" % (time.time() - startTime))

    return totalFailedJobs #Returns number of failed jobs
