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
import sys
import mesos.interface
from mesos.interface import mesos_pb2
import mesos.native
import random
from mesosExecutor import JobTreeMesosExecutor
import logging

log = logging.getLogger( __name__ )

class BadExecutor(JobTreeMesosExecutor):

    def _callCommand(self, command):
        ran = random.randint(1, 10)
        print "random: {}".format(ran)
        if ran < 5:
            result = super(BadExecutor, self)._callCommand(command)
            if result == 1:
                print "ran and failed command {}".format(command)
            return result
        else:
            log.debug("ignoring command: {}".format(command))
            return 1

if __name__ == "__main__":
    log.debug("Starting executor")
    driver = mesos.native.MesosExecutorDriver(BadExecutor())
    sys.exit(0 if driver.run() == mesos_pb2.DRIVER_STOPPED else 1)

