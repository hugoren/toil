#!/usr/bin/env python

# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
import os
import sys
import threading

import mesos.interface
from mesos.interface import mesos_pb2
import mesos.native
from subprocess import call
import pickle
import logging

log = logging.getLogger( __name__ )


class JobTreeMesosExecutor(mesos.interface.Executor):
    """Part of mesos framework, runs on mesos slave. A jobTree job is passed to it via the task.data field,
     and launched via call(jobTree.command). Uses the ExecutorDriver to communicate.
    """

    def registered(self, driver, executorInfo, frameworkInfo, slaveInfo):
        """
        Invoked once the executor driver has been able to successfully connect with Mesos.
        :param driver:
        :param executorInfo:
        :param frameworkInfo:
        :param slaveInfo:
        :return:
        """
        log.debug("Registered with framework")

    def reregistered(self, driver, slaveInfo):
        """
        Invoked when the executor re-registers with a restarted slave.
        :param driver:
        :param slaveInfo:
        :return:
        """
        log.debug("Re-Registered")

    def disconnected(self, driver):
        """
        Invoked when the executor becomes "disconnected" from the slave (e.g., the slave is being restarted due to an upgrade).
        :param driver:
        :return:
        """
        print "disconnected from slave"

    def error(self, driver, message):
        """
        Invoked when a fatal error has occurred with the executor and/or executor driver.
        :param driver:
        :param message:
        :return:
        """
        log.error(message)
        self.frameworkMessage(driver, message)

    def _callCommand(self, command):
        log.debug("running command: {}".format(command))
        return call(command, shell=True)

    def launchTask(self, driver, task):
        """
        Invoked by SchedulerDriver when a task has been launched on this executor
        :param driver:
        :param task:
        :return:
        """
        def _run_task():
            log.debug("Running task %s" % task.task_id.value)
            self._sendUpdate(driver, task, mesos_pb2.TASK_RUNNING)

            jobTreeJob = pickle.loads( task.data )
            os.chdir( jobTreeJob.cwd )

            result = self._callCommand(jobTreeJob.command)

            if result != 0:
                self._sendUpdate(driver, task, mesos_pb2.TASK_FAILED)
            else:
                self._sendUpdate(driver, task, mesos_pb2.TASK_FINISHED)

        # TODO: I think there needs to be a thread.join() somewhere for each thread. Come talk to me about this.
        thread = threading.Thread(target=_run_task)
        thread.start()

    def _sendUpdate(self, driver, task, TASK_STATE):
        log.debug("Sending status update...")
        update = mesos_pb2.TaskStatus()
        update.task_id.value = task.task_id.value
        update.state = TASK_STATE
        driver.sendStatusUpdate(update)
        log.debug("Sent status update")

    def frameworkMessage(self, driver, message):
        """
        Invoked when a framework message has arrived for this executor.
        :param driver:
        :param message:
        :return:
        """
        # Send it back to the scheduler.
        driver.sendFrameworkMessage(message)

if __name__ == "__main__":
    log.debug("Starting executor")
    driver = mesos.native.MesosExecutorDriver(JobTreeMesosExecutor())
    sys.exit(0 if driver.run() == mesos_pb2.DRIVER_STOPPED else 1)
