from threading import *
import sshKeyDist
from utilityFunctions import *
import traceback
import sys
import launcher_version_number
import shlex
import xmlrpclib
import re
import urllib2
import datetime
import os




class LoginProcess():
    """LoginProcess Class."""
            
    class createTunnelThread(Thread):

        def __init__(self,loginprocess,success,failure):
            Thread.__init__(self)
            self.loginprocess = loginprocess
            self._stop = Event()
            self.success=success
            self.failure=failure
    
        def stop(self):
            self.process.stdin.write("exit\n")
            self._stop.set()
        
        def stopped(self):
            return self._stop.isSet()

        def run(self):
            try:
                # Dodgyness ... I can't think of how to determine the remotePortNumber except by adding 5900 to the vnc Display number.
                # I can't think of an easy way to get the vncDisplay number when executing via qsub, but on MASSIVE it will always ben display :1
                if (not self.loginprocess.jobParams.has_key('localPortNumber')):
                    import socket
                    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    sock.bind(('localhost', 0))
                    localPortNumber = sock.getsockname()[1]
                    sock.close()
                    self.loginprocess.localPortNumber = str(localPortNumber)
                    self.loginprocess.jobParams['localPortNumber'] = str(localPortNumber)
                if (not self.loginprocess.jobParams.has_key('vncDisplay')):
                    self.loginprocess.jobParams['vncDisplay']=":1"
                self.loginprocess.jobParams['remotePortNumber'] = str(5900+int(self.loginprocess.jobParams['vncDisplay'].lstrip(':')))
                try:
                    tunnel_cmd = self.loginprocess.tunnelCmd.format(**self.loginprocess.jobParams)
                except KeyError as e:
                    self.loginprocess.cancel("I couldn't determine the correct command to create a tunnel for the VNC session. I was missing the parameter %s"%e)
                    return



                logger_debug('tunnel_cmd: ' + tunnel_cmd)

                # Not 100% sure if this is necessary on Windows vs Linux. Seems to break the
                # Windows version of the launcher, but leaving in for Linux/OSX.
                if sys.platform.startswith("win"):
                    pass
                else:
                    tunnel_cmd = shlex.split(tunnel_cmd)

                self.process = subprocess.Popen(tunnel_cmd,
                    universal_newlines=True,shell=False,stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.PIPE)
                while (not self.stopped()):
                    time.sleep(0.1)
                    line = self.process.stdout.readline()
                    if (line != None):
                        match = re.search(self.loginprocess.tunnelRegEx.format(**self.loginprocess.jobParams),line)
                        if (match and not self.stopped()):
                            self.success()
                            success=True
                    else:
                        if (not success):
                            self.failure()
                    if self.stopped():
                        return
            except Exception as e:
                error_message = "%s"%e
                logger_error('Create tunnel failure: '+ error_message)
                self.failure()
                return

    class getOTPThread(Thread):
        def __init__(self,loginprocess):
            Thread.__init__(self)
            self.loginprocess = loginprocess
            self._stop = Event()
    
        def stop(self):
            logger_debug("stopping the thread that generates the one time password")
            self._stop.set()
        
        def stopped(self):
            return self._stop.isSet()

        def run(self):
            try:
                try:
                    otp_cmd = self.loginprocess.otpCmd.format(**self.loginprocess.jobParams)
                except KeyError as e:
                    self.loginprocess.cancel("Trying to get the One Time password, I was missing a parameter %s"%e)
                    return
                logger_debug("otp command %s"%otp_cmd)
                if sys.platform.startswith("win"):
                    pass
                else:
                    otp_cmd = shlex.split(otp_cmd)

                otpProcess = subprocess.Popen(otp_cmd,
                    universal_newlines=True,shell=False,stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.PIPE)
                stdout,stderr = otpProcess.communicate()
                passwdFound=False
                for line in stdout.splitlines(False):
                    passwd = re.search(self.loginprocess.otpRegEx.format(**self.loginprocess.jobParams),line)
                    if (passwd):
                        self.loginprocess.jobParams.update(passwd.groupdict())
                        passwdFound=True
                        break

            except Exception as e:
                self.loginprocess.cancel("Couldn't execute vncpassword %s"%e)
                return
            if (not passwdFound):
                self.loginprocess.cancel("Couldn't extract a VNC password")
                return
            if (not self.stopped()):
                event=LoginProcess.loginProcessEvent(LoginProcess.EVT_LOGINPROCESS_START_VIEWER,self.loginprocess)
                wx.PostEvent(self.loginprocess.notify_window.GetEventHandler(),event)
                
    class forwardAgentThread(Thread):

        def __init__(self,loginprocess,success,failure):
            Thread.__init__(self)
            self.loginprocess = loginprocess
            self._stop = Event()
            self.success=success
            self.failure=failure
    
        def stop(self):
            logger_debug("stopping the thread that forwards the SSH Agent") 
            self.process.stdin.write("exit\n")
            self._stop.set()
        
        def stopped(self):
            return self._stop.isSet()

        def run(self):

            try:
                agent_cmd = self.loginprocess.agentCmd.format(**self.loginprocess.jobParams)
                logger_debug('agent_cmd: ' + agent_cmd)

                # Not 100% sure if this is necessary on Windows vs Linux. Seems to break the
                # Windows version of the launcher, but leaving in for Linux/OSX.
                if sys.platform.startswith("win"):
                    pass
                else:
                    agent_cmd = shlex.split(agent_cmd)

                self.process = subprocess.Popen(agent_cmd,
                    universal_newlines=True,shell=False,stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.PIPE)
                while (not self.stopped()):
                    time.sleep(0.1)
                    line = self.process.stdout.readline()
                    if (line != None):
                        match = re.search(self.loginprocess.agentRegEx.format(**self.loginprocess.jobParams),line)
                        if (match and not self.stopped()):
                            self.success()
                            success=True
                    else:
                        if (not success):
                            self.failure()
                    if self.stopped():
                        return

            except Exception as e:
                error_message = "%s"%e
                logger_error('forward agent failure: '+ error_message)
                self.failure()

    class SimpleOptionDialog(wx.Dialog):
        def __init__(self, parent, id, title, text, okString, cancelString, OKCallback, CancelCallback):
            wx.Dialog.__init__(self, parent, id, title, style=wx.DEFAULT_FRAME_STYLE ^ wx.RESIZE_BORDER | wx.STAY_ON_TOP)
            self.SetTitle(title)
            self.panel = wx.Panel(self,-1)
            self.label = wx.StaticText(self.panel, -1, text)
            self.Cancel = wx.Button(self.panel,-1,label=cancelString)
            self.OK = wx.Button(self.panel,-1,label=okString)
            self.OKCallback=OKCallback
            self.CancelCallback=CancelCallback

            self.sizer = wx.FlexGridSizer(3, 1)
            self.buttonRow = wx.FlexGridSizer(1, 2, hgap=10)
            self.sizer.Add(self.label)
            self.sizer.Add(wx.StaticText(self.panel, -1, ""))
            self.sizer.Add(self.buttonRow, flag=wx.ALIGN_RIGHT)
            self.buttonRow.Add(self.Cancel)
            self.buttonRow.Add(self.OK)

            self.OK.Bind(wx.EVT_BUTTON,self.onOK)
            self.Cancel.Bind(wx.EVT_BUTTON,self.onCancel)

            self.CenterOnParent()

            self.border = wx.BoxSizer()
            self.border.Add(self.sizer, 0, wx.ALL, 15)
            self.panel.SetSizerAndFit(self.border)
            self.Fit()
            self.password = None
        
        def onOK(self,event):
            self.Close()
            self.Destroy()
            self.OKCallback()

        def onCancel(self,event):
            self.Close()
            self.Destroy()
            self.CancelCallback()

    class startVNCViewer(Thread):
        def __init__(self,loginprocess):
            Thread.__init__(self)
            self.loginprocess = loginprocess
            self._stop = Event()
    
        def stop(self):
            logger_debug("stopping the thread that starts the VNC Viewer")
            self._stop.set()
        
        def stopped(self):
            return self._stop.isSet()

        def run(self):
            wx.CallAfter(self.loginprocess.notify_window.progressDialog.Show, False)
            
            if (self.loginprocess.jobParams.has_key('vncPasswd')):

                try:
                    if sys.platform.startswith("win"):
                        vncCommandString = "\"{vnc}\" /user {username} /autopass /nounixlogin {vncOptionsString} localhost::{localPortNumber}".format(**self.loginprocess.jobParams)
                    else:
                        vncCommandString = "{vnc} -user {username} -autopass -nounixlogin {vncOptionsString} localhost::{localPortNumber}".format(**self.loginprocess.jobParams)
                    self.turboVncProcess = subprocess.Popen(vncCommandString,
                        stdout=subprocess.PIPE, stdin=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True,
                        universal_newlines=True)
                    self.turboVncStdout, self.turboVncStderr = self.turboVncProcess.communicate(input=self.loginprocess.jobParams['vncPasswd'] + "\n")
                    if (not self.stopped()):
                        event=LoginProcess.loginProcessEvent(LoginProcess.EVT_LOGINPROCESS_SHUTDOWN,self.loginprocess)
                        wx.PostEvent(self.loginprocess.notify_window.GetEventHandler(),event)
                        event=LoginProcess.loginProcessEvent(LoginProcess.EVT_LOGINPROCESS_STAT_RUNNING_JOB,self.loginprocess)
                        wx.PostEvent(self.loginprocess.notify_window.GetEventHandler(),event)

                except Exception as e:
                    self.loginprocess.cancel("Couldn't start the vnc viewer: %s"%e)
            else:
                    self.loginprocess.cancel("Couldn't start the vnc viewer. There was no password set")
        
    class startServerThread(Thread):
        def __init__(self,loginprocess):
            Thread.__init__(self)
            self.loginprocess = loginprocess
            self._stop = Event()
    
        def stop(self):
            logger_debug("stopping the thread that starts the VNC viewer")
            self._stop.set()
        
        def stopped(self):
            return self._stop.isSet()

        def run(self):
            sshCmd = self.loginprocess.sshCmd
            (stdout, stderr) = run_ssh_command(sshCmd.format(**self.loginprocess.jobParams), self.loginprocess.startServerCmd.format(**self.loginprocess.jobParams),ignore_errors=True, callback=self.loginprocess.cancel)
            started=False
            import itertools
            messages=parseMessages(self.loginprocess.messageRegexs,stdout,stderr)
            concat=""
            for key in messages.keys():
                concat=concat+messages[key]
            event=None
            if (messages.has_key('error')):
                logger_error("canceling the loginprocess due to errors in the output of the startServer command: %s"%messages)
                self.loginprocess.cancel(concat)
            elif (messages.has_key('warn') or messages.has_key('info')):
                dlg=HelpDialog(self.loginprocess.notify_window, title="MASSIVE/CVL Launcher", name="MASSIVE/CVL Launcher",pos=(200,150),size=(680,290),style=wx.STAY_ON_TOP)
                panel=wx.Panel(dlg)
                sizer=wx.BoxSizer()
                panel.SetSizer(sizer)
                text=wx.StaticText(panel,wx.ID_ANY,label=concat)
                sizer.Add(text,0,wx.ALL,15)
                dlg.addPanel(panel)
                wx.CallAfter(dlg.ShowModal)
            for line  in itertools.chain(stdout.splitlines(False),stderr.splitlines(False)):
                match=re.search(self.loginprocess.startServerRegEx.format(**self.loginprocess.jobParams),line)
                if (match):
                    logger_debug('matched the startServerRegEx %s' % line)
                    self.loginprocess.jobParams.update(match.groupdict())
                    self.loginprocess.started_job.set()
                    started=True
                    break
                else:
                    logger_debug('Did not match the startServerRegEx %s' % line)
            if (not started):
                self.loginprocess.cancel("I was unable to start the VNC server")
                return
            if (not self.stopped()):
                event=LoginProcess.loginProcessEvent(LoginProcess.EVT_LOGINPROCESS_CONNECT_SERVER,self.loginprocess)
                wx.PostEvent(self.loginprocess.notify_window.GetEventHandler(),event)

    class getExecutionHostThread(Thread):
        def __init__(self,loginprocess,nextEvent):
            Thread.__init__(self)
            self.loginprocess = loginprocess
            self._stop = Event()
            self.nextEvent = nextEvent
    
        def stop(self):
            logger_debug("stopping the thread that determines the execution host")
            self._stop.set()
        
        def stopped(self):
            return self._stop.isSet()

        def run(self):
            notStarted=True
            tsleep=0
            sleepperiod=1
            jobRunning=None
            # Make local copies, just because I tired of typing "self.loginprocess."
            runningCmd=self.loginprocess.runningCmd
            runningRegEx=self.loginprocess.runningRegEx
            execHostCmd=self.loginprocess.execHostCmd
            execHostRegEx=self.loginprocess.execHostRegEx
            sshCmd = self.loginprocess.sshCmd
            jobParams=self.loginprocess.jobParams
            while (not jobRunning and not self.stopped()):
                tsleep+=sleepperiod
                if (not self.stopped()):
                    time.sleep(sleepperiod)
                try:
                    (stdout,stderr) = run_ssh_command(sshCmd.format(**jobParams),runningCmd.format(**jobParams),ignore_errors=True)
                except KeyError as e:
                    self.loginprocess.cancel("Trying to check if the job is running yet, I was missing a parameter %s"%e)
                    return
                
                for line in stdout.splitlines(False):
                    if (not self.stopped()):
                        try:
                            regex=runningRegEx.format(**jobParams)
                            logger_debug("searching the output of %s using regex %s"%(runningCmd.format(**jobParams),regex))
                        except KeyError as e:
                            logger_error("Trying to check if the job is running yet, unable to formulate regex, missing parameter %s"%e)
                            self.loginprocess.cancel("Sorry, a catastropic error occured and I was unable to connect to your VNC session")
                            return
                        jobRunning = re.search(regex,line)
                        if (jobRunning):
                            self.loginprocess.jobParams.update(jobRunning.groupdict())
                            break
                        if (not jobRunning and tsleep == 1):
                            sleepperiod=15
                        if (not jobRunning and tsleep > 15 and self.loginprocess.showStartCmd!=None):
                            stdoutRead, stderrRead = run_ssh_command(sshCmd.format(**jobParams),self.loginprocess.showStartCmd.format(**jobParams),ignore_errors=True)
                            if not "00:00:00" in stdoutRead:
                                logger_debug("showstart " + self.loginprocess.jobParams['jobid'] + "...")
                                logger_debug('showstart stderr: ' + stderrRead)
                                logger_debug('showstart stdout: ' + stdoutRead)
                          
                                showstartLines = stdoutRead.splitlines(False)
                                for showstartLine in showstartLines:
                                    if showstartLine.startswith("Estimated Rsv based start"):
                                        showstartLineComponents = showstartLine.split(" on ")
                                        if not showstartLineComponents[1].startswith("-"):
                                            wx.CallAfter(self.loginprocess.updateProgressDialog, 6, "Estimated start: " + showstartLineComponents[1])
                            sleepperiod=30
            # Loop until we figure out which host the vnc server was started on.
            logger_debug('job is running, looking for execHost')
            execHost = None
            jobParams=self.loginprocess.jobParams
            while (not execHost and not self.stopped()):
                try:
                    (stdout,stderr) = run_ssh_command(sshCmd.format(**jobParams),execHostCmd.format(**jobParams),ignore_errors=True)
                except KeyError as e:
                    logger_error("execHostCmd missing parameter %s"%e)
                    self.loginprocess.cancel("Sorry, a catastropic error occured and I was unable to connect to your VNC session")
                lines = stdout.splitlines(False)
                for line in lines:
                    if (not self.stopped()):
                        try:
                            execHost = re.search(execHostRegEx.format(**jobParams),line)
                        except KeyError as e:
                            logger_error("execHostRegEx missing parameter %s"%e)
                            self.loginprocess.cancel("Sorry, a catastropic error occured and I was unable to connect to your VNC session")
                            return
                        if (execHost):
                            self.loginprocess.jobParams.update(execHost.groupdict())
                            break
            if (not self.stopped()):
                wx.PostEvent(self.loginprocess.notify_window.GetEventHandler(),self.nextEvent)

    class killServerThread(Thread):
        def __init__(self,loginprocess,restart):
            Thread.__init__(self)
            self.loginprocess = loginprocess
            self._stop = Event()
            self.restart = restart
    
        def stop(self):
            logger_debug("stopping the killServerThread (this won't really stop killing the server, but it will stop any further actions events being posted)")
            self._stop.set()
        
        def stopped(self):
            return self._stop.isSet()

        def run(self):
            try:
                stdoutRead, stderrRead = run_ssh_command(self.loginprocess.sshCmd.format(**self.loginprocess.jobParams),self.loginprocess.stopCmd.format(**self.loginprocess.jobParams), ignore_errors=True,callback=self.loginprocess.cancel)
            except KeyError as e:
                logger_error("stopCmd missing parameter %s"%e)
                self.loginprocess.cancel("Sorry, an error occured and I was unable to shutdown your VNC session")
                return
            if (not self.stopped()):
                if (self.restart):
                    event=LoginProcess.loginProcessEvent(LoginProcess.EVT_LOGINPROCESS_START_SERVER,self.loginprocess)
                else:
                    event=LoginProcess.loginProcessEvent(LoginProcess.EVT_LOGINPROCESS_NORMAL_TERMINATION,self.loginprocess)
                self.loginprocess.vncJobID=None
                wx.PostEvent(self.loginprocess.notify_window.GetEventHandler(),event)

    class CheckExistingDesktop(Thread):
        def __init__(self,loginprocess,callback_found,callback_notfound):
            Thread.__init__(self)
            self.loginprocess = loginprocess
            self._stop = Event()
            self.loginprocess.joblist=[]
            self.callback_found=callback_found
            self.callback_notfound=callback_notfound
    
        def stop(self):
            self._stop.set()
        
        def stopped(self):
            return self._stop.isSet()

        def run(self):
            self.loginprocess.job=None
            try:
                (stdout,stderrr) = run_ssh_command(self.loginprocess.sshCmd.format(**self.loginprocess.jobParams),self.loginprocess.listAllCmd.format(**self.loginprocess.jobParams),ignore_errors=True)
            except KeyError as e:
                logger_error("listAllCmd missing parameter %s"%e)
                self.loginprocess.cancel("Sorry, an error occured and I was unable to determine if you already have any running desktops")
                return
            lines = stdout.splitlines(False)
            try:
                regex = self.loginprocess.listAllRegEx.format(**self.loginprocess.jobParams)
            except KeyError as e:
                logger_error("listAllRegEx missing parameter %s"%e)
                self.loginprocess.cancel("Sorry, an error occured and I was unable to determine if you already have any running desktops")
                return
            for line in lines:
                match=re.search(regex,line)
                if match:
                    self.loginprocess.joblist.append(match.groupdict())

            # Currently only capabale of dealing with one existing Desktop at a time (as is MASSIVE policy)
            # TODO make a nice dialog here to select which job you are talking about from a list of jobs.
            if (self.loginprocess.joblist!=[]):
                self.loginprocess.job=self.loginprocess.joblist[-1]
                self.loginprocess.jobParams.update(self.loginprocess.job)

            if (not self.stopped()):
                if (self.loginprocess.job !=None):
                    logger_debug("existing desktop found")
                    self.callback_found()
                else:
                    logger_debug("existing desktop NOT found")
                    self.callback_notfound()

    class CheckVNCVerThread(Thread):
        def __init__(self,loginprocess):
            Thread.__init__(self)
            self.loginprocess = loginprocess
            self._stop = Event()
    
        def stop(self):
            logger_debug("stop called on CheckVNCVerThread") 
            self._stop.set()
        
        def stopped(self):
            return self._stop.isSet()
                
        def getTurboVncVersionNumber_Windows(self):
            if sys.platform.startswith("win"):
                key = None
                queryResult = None
                foundTurboVncInRegistry = False
                vnc = r"C:\Program Files\TurboVNC\vncviewer.exe"

                import _winreg

                turboVncVersionNumber = None

                if not foundTurboVncInRegistry:
                    try:
                        # 64-bit Windows installation, 64-bit TurboVNC, HKEY_CURRENT_USER
                        key = _winreg.OpenKey(_winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\TurboVNC 64-bit_is1", 0,  _winreg.KEY_WOW64_64KEY | _winreg.KEY_READ)
                        queryResult = _winreg.QueryValueEx(key, "InstallLocation")
                        vnc = os.path.join(queryResult[0], "vncviewer.exe")
                        queryResult = _winreg.QueryValueEx(key, "DisplayVersion")
                        turboVncVersionNumber = queryResult[0]
                        foundTurboVncInRegistry = True
                    except:
                        foundTurboVncInRegistry = False
                        #wx.CallAfter(sys.stdout.write, "MASSIVE/CVL Launcher v" + launcher_version_number.version_number + "\n")
                        #wx.CallAfter(sys.stdout.write, traceback.format_exc())
                if not foundTurboVncInRegistry:
                    try:
                        # 64-bit Windows installation, 64-bit TurboVNC, HKEY_LOCAL_MACHINE
                        key = _winreg.OpenKey(_winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\TurboVNC 64-bit_is1", 0,  _winreg.KEY_WOW64_64KEY | _winreg.KEY_READ)
                        queryResult = _winreg.QueryValueEx(key, "InstallLocation")
                        vnc = os.path.join(queryResult[0], "vncviewer.exe")
                        queryResult = _winreg.QueryValueEx(key, "DisplayVersion")
                        turboVncVersionNumber = queryResult[0]
                        foundTurboVncInRegistry = True
                    except:
                        foundTurboVncInRegistry = False
                        #wx.CallAfter(sys.stdout.write, "MASSIVE/CVL Launcher v" + launcher_version_number.version_number + "\n")
                        #wx.CallAfter(sys.stdout.write, traceback.format_exc())
                if not foundTurboVncInRegistry:
                    try:
                        # 32-bit Windows installation, 32-bit TurboVNC, HKEY_CURRENT_USER
                        key = _winreg.OpenKey(_winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\TurboVNC_is1", 0, _winreg.KEY_READ)
                        queryResult = _winreg.QueryValueEx(key, "InstallLocation")
                        vnc = os.path.join(queryResult[0], "vncviewer.exe")
                        queryResult = _winreg.QueryValueEx(key, "DisplayVersion")
                        turboVncVersionNumber = queryResult[0]
                        foundTurboVncInRegistry = True
                    except:
                        foundTurboVncInRegistry = False
                        #wx.CallAfter(sys.stdout.write, "MASSIVE/CVL Launcher v" + launcher_version_number.version_number + "\n")
                        #wx.CallAfter(sys.stdout.write, traceback.format_exc())
                if not foundTurboVncInRegistry:
                    try:
                        # 32-bit Windows installation, 32-bit TurboVNC, HKEY_LOCAL_MACHINE
                        key = _winreg.OpenKey(_winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\TurboVNC_is1", 0, _winreg.KEY_READ)
                        queryResult = _winreg.QueryValueEx(key, "InstallLocation")
                        vnc = os.path.join(queryResult[0], "vncviewer.exe")
                        queryResult = _winreg.QueryValueEx(key, "DisplayVersion")
                        turboVncVersionNumber = queryResult[0]
                        foundTurboVncInRegistry = True
                    except:
                        foundTurboVncInRegistry = False
                        #wx.CallAfter(sys.stdout.write, "MASSIVE/CVL Launcher v" + launcher_version_number.version_number + "\n")
                        #wx.CallAfter(sys.stdout.write, traceback.format_exc())
                if not foundTurboVncInRegistry:
                    try:
                        # 64-bit Windows installation, 32-bit TurboVNC, HKEY_CURRENT_USER
                        key = _winreg.OpenKey(_winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\TurboVNC_is1", 0, _winreg.KEY_WOW64_32KEY | _winreg.KEY_READ)
                        queryResult = _winreg.QueryValueEx(key, "InstallLocation")
                        vnc = os.path.join(queryResult[0], "vncviewer.exe")
                        queryResult = _winreg.QueryValueEx(key, "DisplayVersion")
                        turboVncVersionNumber = queryResult[0]
                        foundTurboVncInRegistry = True
                    except:
                        foundTurboVncInRegistry = False
                        #wx.CallAfter(sys.stdout.write, "MASSIVE/CVL Launcher v" + launcher_version_number.version_number + "\n")
                        #wx.CallAfter(sys.stdout.write, traceback.format_exc())
                if not foundTurboVncInRegistry:
                    try:
                        # 64-bit Windows installation, 32-bit TurboVNC, HKEY_LOCAL_MACHINE
                        key = _winreg.OpenKey(_winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\TurboVNC_is1", 0, _winreg.KEY_WOW64_32KEY | _winreg.KEY_READ)
                        queryResult = _winreg.QueryValueEx(key, "InstallLocation")
                        vnc = os.path.join(queryResult[0], "vncviewer.exe")
                        queryResult = _winreg.QueryValueEx(key, "DisplayVersion")
                        turboVncVersionNumber = queryResult[0]
                        foundTurboVncInRegistry = True
                    except:
                        foundTurboVncInRegistry = False
                        #wx.CallAfter(sys.stdout.write, "MASSIVE/CVL Launcher v" + launcher_version_number.version_number + "\n")
                        #wx.CallAfter(sys.stdout.write, traceback.format_exc())

            return (vnc, turboVncVersionNumber)

        def getTurboVncVersionNumber(self,vnc):
            self.turboVncVersionNumber = "0.0"

            turboVncVersionNumberCommandString = vnc + " -help"
            proc = subprocess.Popen(turboVncVersionNumberCommandString,
                stdout=subprocess.PIPE, stdin=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True,
                universal_newlines=True)
            turboVncStdout, turboVncStderr = proc.communicate(input="\n")
            if turboVncStderr != None:
                logger_debug("turboVncStderr: " + turboVncStderr)
            turboVncVersionNumberComponents = turboVncStdout.split(" v")
            turboVncVersionNumberComponents = turboVncVersionNumberComponents[1].split(" (build")
            turboVncVersionNumber = turboVncVersionNumberComponents[0].strip()

            # Check TurboVNC flavour (X11 or Java) for non-Windows platforms:
            turboVncFlavourTestCommandString = "file /opt/TurboVNC/bin/vncviewer | grep -q ASCII"
            proc = subprocess.Popen(turboVncFlavourTestCommandString,
                stdout=subprocess.PIPE, stdin=subprocess.PIPE, stderr=subprocess.STDOUT, shell=True,
                universal_newlines=True)
            stdout, stderr = proc.communicate(input="\n")
            if stderr != None:
                logger_debug('turboVncFlavour stderr: ' + stderr)
            if proc.returncode==0:
                logger_debug("Java version of TurboVNC Viewer is installed.")
                turboVncFlavour = "Java"
            else:
                logger_debug("X11 version of TurboVNC Viewer is installed.")
                turboVncFlavour = "X11"
            
            return (vnc,turboVncVersionNumber,turboVncFlavour)

        def showTurboVncNotFoundMessageDialog(self,turboVncLatestVersion):

            turboVncNotFoundDialog = HelpDialog(self.loginprocess.notify_window, title="MASSIVE/CVL Launcher", name="MASSIVE/CVL Launcher",pos=(200,150),size=(680,290),style=wx.STAY_ON_TOP)

            turboVncNotFoundPanel = wx.Panel(turboVncNotFoundDialog)
            turboVncNotFoundPanelSizer = wx.FlexGridSizer(rows=4, cols=1, vgap=5, hgap=5)
            turboVncNotFoundPanel.SetSizer(turboVncNotFoundPanelSizer)
            turboVncNotFoundTitleLabel = wx.StaticText(turboVncNotFoundPanel,
                label = "MASSIVE/CVL Launcher")
            font = wx.SystemSettings.GetFont(wx.SYS_DEFAULT_GUI_FONT)
            font.SetPointSize(14)
            font.SetWeight(wx.BOLD)
            turboVncNotFoundTitleLabel.SetFont(font)
            turboVncNotFoundPanelSizer.Add(wx.StaticText(turboVncNotFoundPanel))
            turboVncNotFoundPanelSizer.Add(turboVncNotFoundTitleLabel, flag=wx.EXPAND)
            turboVncNotFoundPanelSizer.Add(wx.StaticText(turboVncNotFoundPanel))
            turboVncNotFoundTextLabel1 = wx.StaticText(turboVncNotFoundPanel,
                label = "TurboVNC not found.\n" +
                        "Please download from:\n")
            font = wx.SystemSettings.GetFont(wx.SYS_DEFAULT_GUI_FONT)
            if sys.platform.startswith("darwin"):
                font.SetPointSize(11)
            else:
                font.SetPointSize(9)
            turboVncNotFoundTextLabel1.SetFont(font)
            turboVncNotFoundPanelSizer.Add(turboVncNotFoundTextLabel1, flag=wx.EXPAND)
            turboVncNotFoundHyperlink = wx.HyperlinkCtrl(turboVncNotFoundPanel,
                id = wx.ID_ANY,
                label = TURBOVNC_BASE_URL + turboVncLatestVersion,
                url = TURBOVNC_BASE_URL + turboVncLatestVersion)
            font = wx.SystemSettings.GetFont(wx.SYS_DEFAULT_GUI_FONT)
            if sys.platform.startswith("darwin"):
                font.SetPointSize(11)
            else:
                font.SetPointSize(8)
            turboVncNotFoundHyperlink.SetFont(font)
            turboVncNotFoundPanelSizer.Add(turboVncNotFoundHyperlink, border=10, flag=wx.LEFT|wx.RIGHT|wx.BORDER)
            turboVncNotFoundPanelSizer.Add(wx.StaticText(turboVncNotFoundPanel))

            turboVncNotFoundDialog.addPanel(turboVncNotFoundPanel)
            turboVncNotFoundDialog.ShowModal()
    
        def run(self):
            # Check for TurboVNC

            # Check for the latest version of TurboVNC on the launcher web page.
            # Don't bother to do this if we couldn't get to the Massive website earlier.
            # Somewhat strangely, if the earlier check to Massive failed due to a 404 (or similar)
            # then we get wxPython problems here:
            # Traceback (most recent call last):
            #   File "/opt/sw/32bit/debian/python/2.7.3/lib/python2.7/site-packages/wx-2.8-gtk2-unicode/wx/_core.py", line 14665, in <lambda>
            #     lambda event: event.callable(*event.args, **event.kw) )
            #   File "launcher.py", line 1549, in error_dialog
            #     "MASSIVE/CVL Launcher", wx.OK | wx.ICON_INFORMATION)
            #   File "/opt/sw/32bit/debian/python/2.7.3/lib/python2.7/site-packages/wx-2.8-gtk2-unicode/wx/_windows.py", line 2914, in __init__
            #     _windows_.MessageDialog_swiginit(self,_windows_.new_MessageDialog(*args, **kwargs))
            # TypeError: in method 'new_MessageDialog', expected argument 1 of type 'wxWindow *'

            if self.loginprocess.notify_window.contacted_massive_website:
                try:
                    myHtmlParser = MyHtmlParser('TurboVncLatestVersionNumber')
                    feed = urllib2.urlopen(LAUNCHER_URL, timeout=10)
                    html = feed.read()
                    myHtmlParser.feed(html)
                    myHtmlParser.close()
                except Exception as e:
                    logger_debug("Exception while checking TurboVNC version number: " + str(e))

                    def error_dialog():
                        dlg = wx.MessageDialog(self.notify_window, "Error: Unable to contact MASSIVE website to check the TurboVNC version number.\n\n" +
                                                "The launcher cannot continue.\n",
                                        "MASSIVE/CVL Launcher", wx.OK | wx.ICON_INFORMATION)
                        dlg.ShowModal()
                        dlg.Destroy()
                        # If we can't contact the MASSIVE website, it's probably because
                        # there's no active network connection, so don't try to submit
                        # the log to cvl.massive.org.au
                        dump_log(self.notify_window,submit_log=False)
                        sys.exit(1)
                    wx.CallAfter(error_dialog)

                turboVncLatestVersion = myHtmlParser.latestVersionNumber
            else:
                turboVncLatestVersion = ''
            turboVncLatestVersion = ''

            turboVncVersionNumber = None

            if sys.platform.startswith("win"):
                (vnc, turboVncVersionNumber) = self.getTurboVncVersionNumber_Windows()
                if os.path.exists(vnc):
                    logger_debug("TurboVNC was found in " + vnc)
                else:
                    self.loginprocess.cancel("TurboVNC not found")
                turboVncFlavour = None
            else:
                vnc = "/opt/TurboVNC/bin/vncviewer"
                if os.path.exists(vnc):
                    logger_debug("TurboVNC was found in " + vnc)
                else:
                    self.loginprocess.cancel()
                    wx.CallAfter(self.showTurboVncNotFoundMessageDialog,turboVncLatestVersion)
                    return
                (vnc,turboVncVersionNumber,turboVncFlavour) = self.getTurboVncVersionNumber(vnc)

            if turboVncVersionNumber is None:
                def error_dialog():
                    dlg = wx.MessageDialog(self.loginprocess.notify_window, "Error: Could not determine TurboVNC version number.\n\n" +
                                            "The launcher cannot continue.\n",
                                    "MASSIVE/CVL Launcher", wx.OK | wx.ICON_INFORMATION)
                    dlg.ShowModal()
                    dlg.Destroy()
                    dump_log(self.loginprocess.notify_window)
                    sys.exit(1)

                if (self.loginprocess.notify_window.progressDialog != None):
                    wx.CallAfter(self.loginprocess.notify_window.progressDialog.Hide)
                    wx.CallAfter(self.loginprocess.notify_window.progressDialog.Show, False)
                    wx.CallAfter(self.loginprocess.notify_window.progressDialog.Destroy)
                    self.loginprocess.notify_window.progressDialog = None

                wx.CallAfter(error_dialog)
                return


            logger_debug("TurboVNC viewer version number = " + turboVncVersionNumber)
            
            #self.loginprocess.turboVncVersionNumber = turboVncVersionNumber
            self.loginprocess.jobParams['vnc'] = vnc
            self.loginprocess.jobParams['turboVncFlavour'] = turboVncFlavour
            self.loginprocess.jobParams['vncOptionsString'] = self.loginprocess.buildVNCOptionsString()

            if turboVncVersionNumber.startswith("0.") or turboVncVersionNumber.startswith("1.0"):
                def showOldTurboVncWarningMessageDialog():
                    dlg = wx.MessageDialog(self.notify_window, "Warning: Using a TurboVNC viewer earlier than v1.1 means that you will need to enter your password twice.\n",
                                    "MASSIVE/CVL Launcher", wx.OK | wx.ICON_INFORMATION)
                    dlg.ShowModal()
                    dlg.Destroy()
                    logger_debug("vnc viewer found, user warned about old version")
                wx.CallAfter(showOldTurboVncWarningMessageDialog)
            else:
                logger_debug("vnc viewer found")
            if (not self.stopped()):
                event=LoginProcess.loginProcessEvent(LoginProcess.EVT_LOGINPROCESS_DISTRIBUTE_KEY,self.loginprocess)
                wx.PostEvent(self.loginprocess.notify_window.GetEventHandler(),event)

    class loginProcessEvent(wx.PyCommandEvent):
        def __init__(self,id,loginprocess,string=""):
            wx.PyCommandEvent.__init__(self,LoginProcess.myEVT_CUSTOM_LOGINPROCESS,id)
            self.loginprocess = loginprocess
            self.string = string

        def showReconnectDialog(event):
            if (event.GetId() == LoginProcess.EVT_LOGINPROCESS_RECONNECT_DIALOG):
                wx.CallAfter(event.loginprocess.updateProgressDialog, 4,"Do you want to reconnect to an existing desktop?")
                ReconnectCallback=lambda: wx.PostEvent(event.loginprocess.notify_window.GetEventHandler(),LoginProcess.loginProcessEvent(LoginProcess.EVT_LOGINPROCESS_CONNECT_SERVER,event.loginprocess))
                NewDesktopCallback=lambda: wx.PostEvent(event.loginprocess.notify_window.GetEventHandler(),LoginProcess.loginProcessEvent(LoginProcess.EVT_LOGINPROCESS_RESTART_SERVER,event.loginprocess))
                timeRemaining=event.loginprocess.timeRemaining()
                if (timeRemaining != None):
                    hours, remainder = divmod(timeRemaining, 3600)
                    minutes, seconds = divmod(remainder, 60)
                    if (hours > 1):
                        timestring = "%s hours and %s minutes"%(hours,minutes)
                    elif (hours) == 1:
                        timestring = "%s hour and %s minutes"%(hours,minutes)
                    elif (minutes > 1):
                        timestring = "%s minutes"%minutes
                    elif (minutes == 1):
                        timestring = "%s minute"%minutes
                    else:
                        timestring = "%s minutes"%minutes
                    dialog=LoginProcess.SimpleOptionDialog(event.loginprocess.notify_window,-1,"Reconnect to Existing Desktop","An Existing Desktop was found. It has %s remaining. Would you like to reconnect or kill it and start a new desktop"%timestring,"Reconnect","New Desktop",ReconnectCallback,NewDesktopCallback)
                else:
                    dialog=LoginProcess.SimpleOptionDialog(event.loginprocess.notify_window,-1,"Reconnect to Existing Desktop","An Existing Desktop was found, would you like to reconnect or kill it and start a new desktop","Reconnect","New Desktop",ReconnectCallback,NewDesktopCallback)
                wx.CallAfter(dialog.ShowModal)
            else:
                event.Skip()

        def normalTermination(event):
            # This event is generated if we shutdown the VNC server upon exit. Its basically a no-op, and moves onto processing the shutdown sequence of events
            if (event.GetId() == LoginProcess.EVT_LOGINPROCESS_NORMAL_TERMINATION):
                logger_debug("caught an EVT_LOGINPROCESS_NORMAL_TERMINATION")
                wx.PostEvent(event.loginprocess.notify_window.GetEventHandler(),LoginProcess.loginProcessEvent(LoginProcess.EVT_LOGINPROCESS_COMPLETE,event.loginprocess))
            else:
                event.Skip()

        def complete(event):
            if (event.GetId() == LoginProcess.EVT_LOGINPROCESS_COMPLETE):
                logger_debug("caught an EVT_LOGINPROCESS_COMPLETE")
                if event.loginprocess.autoExit:
                    os._exit(0)
            else:
                event.Skip()

        def statRunningJob(event):
            if (event.GetId() == LoginProcess.EVT_LOGINPROCESS_STAT_RUNNING_JOB):
                event=LoginProcess.loginProcessEvent(LoginProcess.EVT_LOGINPROCESS_QUESTION_KILL_SERVER,event.loginprocess)
                t = LoginProcess.CheckExistingDesktop(event.loginprocess,lambda: wx.PostEvent(event.loginprocess.notify_window.GetEventHandler(),event),lambda: wx.PostEvent(event.loginprocess.notify_window.GetEventHandler(),event))
                t.setDaemon(False)
                t.start()
                event.loginprocess.threads.append(t)
            else:
                event.Skip()

        def showKillServerDialog(event):
            if (event.GetId() == LoginProcess.EVT_LOGINPROCESS_QUESTION_KILL_SERVER):
                KillCallback=lambda: wx.PostEvent(event.loginprocess.notify_window.GetEventHandler(),LoginProcess.loginProcessEvent(LoginProcess.EVT_LOGINPROCESS_KILL_SERVER,event.loginprocess))
                NOOPCallback=lambda: wx.PostEvent(event.loginprocess.notify_window.GetEventHandler(),LoginProcess.loginProcessEvent(LoginProcess.EVT_LOGINPROCESS_COMPLETE,event.loginprocess))
                dialog = None
                timeRemaining=event.loginprocess.timeRemaining()
                if (timeRemaining != None):
                    hours, remainder = divmod(timeRemaining, 3600)
                    minutes, seconds = divmod(remainder, 60)
                    if (hours > 1):
                        timestring = "%s hours and %s minutes"%(hours,minutes)
                    elif (hours) == 1:
                        timestring = "%s hour and %s minutes"%(hours,minutes)
                    elif (minutes > 1):
                        timestring = "%s minutes"%minutes
                    elif (minutes == 1):
                        timestring = "%s minute"%minutes
                    else:
                        timestring = "%s minutes"%minutes
                    dialog=LoginProcess.SimpleOptionDialog(event.loginprocess.notify_window,-1,"Stop the Desktop?","Would you like to leave the desktop running so you can reconnect later?\nIt has %s remaining."%timestring,"Stop the desktop","Leave it running",KillCallback,NOOPCallback)
                elif ("m1" not in event.loginprocess.loginParams['loginHost'] and "m2" not in event.loginprocess.loginParams['loginHost']):
                    dialog=LoginProcess.SimpleOptionDialog(event.loginprocess.notify_window,-1,"Stop the Desktop?","Would you like to leave the desktop running so you can reconnect later?","Stop the desktop","Leave it running",KillCallback,NOOPCallback)
                if dialog:
                    wx.CallAfter(dialog.ShowModal)
                else:
                    # Presumably, the user has already ended their MASSIVE session, so there is no need to ask whether they want to stop it.
                    wx.CallAfter(NOOPCallback)
            else:
                event.Skip()

        def connectServer(event):
            if (event.GetId() == LoginProcess.EVT_LOGINPROCESS_CONNECT_SERVER):
                wx.CallAfter(event.loginprocess.updateProgressDialog, 6,"Getting the node name")
                logger_debug("caught event CONNECT_SERVER")
                event=LoginProcess.loginProcessEvent(LoginProcess.EVT_LOGINPROCESS_START_TUNNEL,event.loginprocess)
                t = LoginProcess.getExecutionHostThread(event.loginprocess,event)
                t.setDaemon(False)
                t.start()
                event.loginprocess.threads.append(t)
            else:
                event.Skip()


        def killServer(event):
            if (event.GetId() == LoginProcess.EVT_LOGINPROCESS_RESTART_SERVER or event.GetId() == LoginProcess.EVT_LOGINPROCESS_KILL_SERVER):
                wx.CallAfter(event.loginprocess.updateProgressDialog, 4,"Stopping the existing desktop session")
                if (event.GetId() == LoginProcess.EVT_LOGINPROCESS_RESTART_SERVER):
                    logger_debug("caught an EVT_LOGINPROCESS_RESTART_SERVER")
                    restart=True
                else:
                    logger_debug("caught an EVT_LOGINPROCESS_KILL_SERVER")
                    restart=False
                t = LoginProcess.killServerThread(event.loginprocess,restart)
                t.setDaemon(False)
                t.start()
                event.loginprocess.threads.append(t)
            else:
                event.Skip()

        def startServer(event):
            if (event.GetId() == LoginProcess.EVT_LOGINPROCESS_START_SERVER):
                wx.CallAfter(event.loginprocess.updateProgressDialog, 5,"Starting a new desktop session")
                logger_debug("caught an EVT_LOGINPROCESS_START_SERVER")
                t = LoginProcess.startServerThread(event.loginprocess)
                t.setDaemon(False)
                t.start()
                event.loginprocess.threads.append(t)
            else:
                event.Skip()
    
        def checkVNCVer(event):
            if (event.GetId() == LoginProcess.EVT_LOGINPROCESS_CHECK_VNC_VER):
                logger_debug("caught an EVT_LOGINPROCESS_CHECK_VNC_VER")
                wx.CallAfter(event.loginprocess.updateProgressDialog, 1,"Checking VNC Version")
                t = LoginProcess.CheckVNCVerThread(event.loginprocess)
                t.setDaemon(False)
                t.start()
                event.loginprocess.threads.append(t)
                logger_debug("starting a thread to find the VNC Viewer")
            else:
                event.Skip()

        def distributeKey(event):
            if (event.GetId() == LoginProcess.EVT_LOGINPROCESS_DISTRIBUTE_KEY):
                wx.CallAfter(event.loginprocess.updateProgressDialog, 2,"Configuring Authorisation")
                event.loginprocess.skd = sshKeyDist.KeyDist(event.loginprocess.jobParams['username'],event.loginprocess.jobParams['loginHost'],event.loginprocess.notify_window,event.loginprocess.sshpaths)
                successevent=LoginProcess.loginProcessEvent(LoginProcess.EVT_LOGINPROCESS_CHECK_RUNNING_SERVER,event.loginprocess)
                event.loginprocess.skd.distributeKey(lambda: wx.PostEvent(event.loginprocess.notify_window.GetEventHandler(),successevent),
                                                     event.loginprocess.cancel)
            else:
                event.Skip()


        def checkRunningServer(event):
            if (event.GetId() == LoginProcess.EVT_LOGINPROCESS_CHECK_RUNNING_SERVER):
                logger_debug("caught LOGINPROCESS_CHECK_RUNNING_SERVER event")
                event.loginprocess.skd = None # SSH key distritbution is complete at this point.
                wx.CallAfter(event.loginprocess.updateProgressDialog, 3,"Looking for an existing desktop to connect to")
                reconnectdialogevent=LoginProcess.loginProcessEvent(LoginProcess.EVT_LOGINPROCESS_RECONNECT_DIALOG,event.loginprocess)
                newdesktopevent=LoginProcess.loginProcessEvent(LoginProcess.EVT_LOGINPROCESS_START_SERVER,event.loginprocess)
                t = LoginProcess.CheckExistingDesktop(event.loginprocess,lambda: wx.PostEvent(event.loginprocess.notify_window.GetEventHandler(),reconnectdialogevent),lambda: wx.PostEvent(event.loginprocess.notify_window.GetEventHandler(),newdesktopevent))
                t.setDaemon(False)
                t.start()
                event.loginprocess.threads.append(t)
            else:
                event.Skip()

        def forwardAgent(event):
            if (event.GetId() == LoginProcess.EVT_LOGINPROCESS_FORWARD_AGENT):
                logger_debug("received FORWARD_AGENT event")
                wx.CallAfter(event.loginprocess.updateProgressDialog, 8,"Setting up SSH Agent forwarding")
                successCallback = lambda: wx.PostEvent(event.loginprocess.notify_window.GetEventHandler(),LoginProcess.loginProcessEvent(LoginProcess.EVT_LOGINPROCESS_GET_OTP,event.loginprocess))
                failCallback = lambda: event.loginprocess.cancel("Unable to forward the ssh agent")
                t = LoginProcess.forwardAgentThread(event.loginprocess,successCallback,failCallback)
                t.setDaemon(False)
                t.start()
                event.loginprocess.threads.append(t)
            else:
                event.Skip()
        
        def startTunnel(event):
            if (event.GetId() == LoginProcess.EVT_LOGINPROCESS_START_TUNNEL):
                wx.CallAfter(event.loginprocess.updateProgressDialog, 7,"Starting the tunnel")
                logger_debug("received START_TUNNEL event")
                event.loginprocess.localPortNumber = "0" # Request ephemeral port.
                testRun = False
                successCallback = lambda: wx.PostEvent(event.loginprocess.notify_window.GetEventHandler(),LoginProcess.loginProcessEvent(LoginProcess.EVT_LOGINPROCESS_FORWARD_AGENT,event.loginprocess))
                failCallback = lambda: event.loginprocess.cancel("Unable to start the tunnel for some reason")
                t = LoginProcess.createTunnelThread(event.loginprocess,successCallback,failCallback)
                t.setDaemon(False)
                t.start()
                event.loginprocess.threads.append(t)
            else:
                event.Skip()

        def getVNCPassword(event):
            if (event.GetId() == LoginProcess.EVT_LOGINPROCESS_GET_OTP):
                wx.CallAfter(event.loginprocess.updateProgressDialog, 9,"Getting the one time password for the VNC server")
                logger_debug("received GET_OTP event")
                t = LoginProcess.getOTPThread(event.loginprocess)
                t.setDaemon(False)
                t.start()
                event.loginprocess.threads.append(t)
            else:
                event.Skip()

        def startViewer(event):
            if (event.GetId() == LoginProcess.EVT_LOGINPROCESS_START_VIEWER):
                wx.CallAfter(event.loginprocess.updateProgressDialog, 9,"Starting the VNC viewer")
                logger_debug("received START_VIEWER event")
                t = LoginProcess.startVNCViewer(event.loginprocess)
                t.setDaemon(False)
                t.start()
                event.loginprocess.threads.append(t)
            else:
                event.Skip()

        def shutdown(event):
            if (event.GetId() == LoginProcess.EVT_LOGINPROCESS_SHUTDOWN):
                logger_debug('caught LOGINPROCESS_SHUTDOWN')
                for t in event.loginprocess.threads:
                    try:
                        t.stop()
                    except:
                        pass
                # Throw away the thread references. We've done all we can to ask them to stop at this point.
                event.loginprocess.threads=[]
                if (event.loginprocess.notify_window.progressDialog != None):
                    wx.CallAfter(event.loginprocess.notify_window.progressDialog.Hide)
                    wx.CallAfter(event.loginprocess.notify_window.progressDialog.Show, False)
                    wx.CallAfter(event.loginprocess.notify_window.progressDialog.Destroy)
                    event.loginprocess.notify_window.progressDialog = None
                logger_debug("all threads stopped and joined")
            else:
                event.Skip()

        def cancel(event):
            if (event.GetId() == LoginProcess.EVT_LOGINPROCESS_CANCEL):
                if event.loginprocess.started_job.isSet():
                    t = LoginProcess.killServerThread(event.loginprocess,False)
                    t.setDaemon(True)
                    t.start()
                    event.loginprocess.threads.append(t)
                logger_debug("caught LOGINPROCESS_CANCEL")
                if (event.loginprocess.skd!=None): 
                        event.loginprocess.skd.cancel()
                newevent=LoginProcess.loginProcessEvent(LoginProcess.EVT_LOGINPROCESS_SHUTDOWN,event.loginprocess)
                wx.PostEvent(event.loginprocess.notify_window.GetEventHandler(),newevent)
                if (event.string!=""):
                    dlg=HelpDialog(event.loginprocess.notify_window,title="MASSIVE/CVL Launcher", name="MASSIVE/CVL Launcher",pos=(200,150),size=(680,290),style=wx.STAY_ON_TOP)
                    panel=wx.Panel(dlg)
                    sizer=wx.BoxSizer()
                    panel.SetSizer(sizer)
                    text=wx.StaticText(panel,wx.ID_ANY,label=event.string)
                    sizer.Add(text,0,wx.ALL,15)
                    dlg.addPanel(panel)
                    wx.CallAfter(dlg.ShowModal)
            else:
                event.Skip()

    myEVT_CUSTOM_LOGINPROCESS=None
    EVT_CUSTOM_LOGINPROCESS=None
    def __init__(self,username,host,resolution,cipher,notifywindow,sshpaths,project=None,hours=None,nodes=1,usePBS=True,directConnect=False,autoExit=False,fastInterface="-ib"):
        LoginProcess.myEVT_CUSTOM_LOGINPROCESS=wx.NewEventType()
        LoginProcess.EVT_CUSTOM_LOGINPROCESS=wx.PyEventBinder(self.myEVT_CUSTOM_LOGINPROCESS,1)
        self.notify_window = notifywindow
        self.loginParams={}
        self.jobParams={}
        self.loginParams['username']=username
        self.loginParams['loginHost']=host
        self.loginParams['project']=project
        self.loginParams['sshBinary']=sshpaths.sshBinary
        self.jobParams['resolution']=resolution
        self.jobParams['cipher']=cipher
        self.jobParams.update(self.loginParams)
        self.sshpaths=sshpaths
        self.threads=[]
        self.jobParams['project']=project
        self.jobParams['hours']=hours
        self.jobParams['wallseconds']=int(hours)*60*60
        self.jobParams['nodes']=nodes
        self._canceled=threading.Event()
        self.usePBS=usePBS
        self.directConnect = directConnect
        self.autoExit = autoExit
        self.sshCmd = '{sshBinary} -A -T -o PasswordAuthentication=no -o PubkeyAuthentication=yes -o StrictHostKeyChecking=yes -l {username} {loginHost} '
        self.sshTunnelProcess=None
        self.sshAgentProcess=None
        self.fastInterface="-ib"
        self.joblist=[]
        self.started_job=threading.Event()
        self.skd=None


        # output from startServerCmd that matches and of these regular expressions will pop up in a window for the user
        self.messageRegexs=[re.compile("^INFO:(?P<info>.*(?:\n|\r\n?))",re.MULTILINE),re.compile("^WARN:(?P<warn>.*(?:\n|\r\n?))",re.MULTILINE),re.compile("^ERROR:(?P<error>.*(?:\n|\r\n?))",re.MULTILINE)]
        if ("m1" in self.loginParams['loginHost'] or "m2" in self.loginParams['loginHost']):
            self.listAllCmd='qstat -u {username}'
            self.listAllRegEx='^\s*(?P<jobid>(?P<jobidNumber>[0-9]+).\S+)\s+{username}\s+(?P<queue>\S+)\s+(?P<jobname>desktop_\S+)\s+(?P<sessionID>\S+)\s+(?P<nodes>\S+)\s+(?P<tasks>\S+)\s+(?P<mem>\S+)\s+(?P<reqTime>\S+)\s+(?P<state>[^C])\s+(?P<elapTime>\S+)\s*$'
            self.runningCmd='qstat -u {username}'
            self.runningRegEx='^\s*(?P<jobid>{jobid})\s+{username}\s+(?P<queue>\S+)\s+(?P<jobname>desktop_\S+)\s+(?P<sessionID>\S+)\s+(?P<nodes>\S+)\s+(?P<tasks>\S+)\s+(?P<mem>\S+)\s+(?P<reqTime>\S+)\s+(?P<state>R)\s+(?P<elapTime>\S+)\s*$'
            self.stopCmd='qdel {jobid}'
            self.execHostCmd='qpeek {jobidNumber}'
            self.execHostRegEx='\s*To access the desktop first create a secure tunnel to (?P<execHost>\S+)\s*$'
            self.startServerCmd="/usr/local/desktop/request_visnode.sh {project} {hours} {nodes} True False False"
            self.startServerRegEx="^(?P<jobid>(?P<jobidNumber>[0-9]+)\.\S+)\s*$"
            self.showStartCmd="showstart {jobid}"
        elif ("cvllogin" in self.loginParams['loginHost']):
            update={}
            update['loginHost']="118.138.241.53"
            self.loginParams.update(update)
            self.jobParams.update(self.loginParams)
            self.directConnect=True
            self.execHostCmd='\"module load pbs ; qstat -f {jobidNumber} | grep exec_host | sed \'s/\ \ */\ /g\' | cut -f 4 -d \' \' | cut -f 1 -d \'/\' | xargs -iname hostn name | grep address | sed \'s/\ \ */\ /g\' | cut -f 3 -d \' \'\"'
            self.execHostRegEx='^\s*(?P<execHost>\S+)\s*$'
            self.listAllCmd='\"module load pbs ; module load maui ; qstat | grep {username}\"'
            self.listAllRegEx='^\s*(?P<jobid>(?P<jobidNumber>[0-9]+)\.\S+)\s+(?P<jobname>desktop_\S+)\s+{username}\s+(?P<elapTime>\S+)\s+(?P<state>R)\s+(?P<queue>\S+)\s*$'
            self.runningCmd='\"module load pbs ; module load maui ; qstat | grep {username}\"'
            self.runningRegEx='^\s*(?P<jobid>{jobidNumber}\.\S+)\s+(?P<jobname>desktop_\S+)\s+{username}\s+(?P<elapTime>\S+)\s+(?P<state>R)\s+(?P<queue>\S+)\s*$'
            self.startServerCmd="\"module load pbs ; module load maui ; echo \'module load pbs ; /usr/local/bin/vncsession --vnc turbovnc --geometry {resolution} ; sleep {wallseconds}\' |  qsub -l nodes=1:ppn=1,walltime={wallseconds} -N desktop_{username}\""
            self.startServerRegEx="^(?P<jobid>(?P<jobidNumber>[0-9]+)\.\S+)\s*$"
            self.stopCmd='\"module load pbs ; module load maui ; qdel {jobidNumber}\"'
            self.showStartCmd=None

        else:
            self.listAllCmd='"module load turbovnc ; vncserver -list"'
            self.listAllRegEx='^(?P<vncDisplay>:[0-9]+)\s*(?P<vncPID>[0-9]+)\s*$'
            self.runningCmd='"module load turbovnc ; vncserver -list"'
            self.runningRegEx='^(?P<vncDisplay>{vncDisplay})\s*(?P<vncPID>[0-9]+)\s*$'
            self.stopCmd='"module load turbovnc ; vncserver -kill {vncDisplay}"'
            self.execHostCmd='echo execHost: {loginHost}'
            self.execHostRegEx='^\s*execHost: (?P<execHost>\S+)\s*$'
            self.startServerCmd = "vncsession --vnc turbovnc --geometry {resolution}"
            self.startServerRegEx="^.*started on display \S+(?P<vncDisplay>:[0-9]+).*$"
            self.showStartCmd=None

        if (not self.directConnect):
            self.agentCmd='{sshBinary} -A -c {cipher} -t -t -oStrictHostKeyChecking=yes -l {username} {loginHost} \"/usr/bin/ssh -A {execHost} \\"echo agent_hello; bash \\"\"'
            self.agentRegEx='agent_hello'
            self.tunnelCmd='{sshBinary} -A -c {cipher} -t -t -oStrictHostKeyChecking=yes -L {localPortNumber}:{execHost}:{remotePortNumber} -l {username} {loginHost} "echo tunnel_hello; bash"'
            self.tunnelRegEx='tunnel_hello'
            self.otpCmd = '{sshBinary} -A -c {cipher} -t -t -oStrictHostKeyChecking=yes -l {username} {loginHost} \"/usr/bin/ssh {execHost} \\"module load turbovnc ; vncpasswd -o -display localhost{vncDisplay} \\"\"'
            self.otpRegEx='^\s*Full control one-time password: (?P<vncPasswd>[0-9]+)\s*$'
        else:
        # I've disabled StrickHostKeyChecking here temporarily untill all CVL vms are added a a most known hosts file.
            self.agentCmd='{sshBinary} -A -c {cipher} -t -t -oStrictHostKeyChecking=no -l {username} {execHost} "echo agent_hello; bash "'
            self.agentRegEx='agent_hello'
            self.tunnelCmd='{sshBinary} -A -c {cipher} -t -t -oStrictHostKeyChecking=no -L {localPortNumber}:localhost:{remotePortNumber} -l {username} {execHost} "echo tunnel_hello; bash"'
            self.tunnelRegEx='tunnel_hello'
            self.otpCmd = '{sshBinary} -A -c {cipher} -t -t -oStrictHostKeyChecking=no -l {username} {execHost} "module load turbovnc ; vncpasswd -o -display localhost{vncDisplay}"'
            self.otpRegEx='^\s*Full control one-time password: (?P<vncPasswd>[0-9]+)\s*$'

        LoginProcess.EVT_LOGINPROCESS_CHECK_VNC_VER = wx.NewId()
        LoginProcess.EVT_LOGINPROCESS_DISTRIBUTE_KEY = wx.NewId()
        LoginProcess.EVT_LOGINPROCESS_CHECK_RUNNING_SERVER = wx.NewId()
        LoginProcess.EVT_LOGINPROCESS_GET_OTP = wx.NewId()
        LoginProcess.EVT_LOGINPROCESS_START_TUNNEL = wx.NewId()
        LoginProcess.EVT_LOGINPROCESS_RUN_VNCVIEWER = wx.NewId()
        LoginProcess.EVT_LOGINPROCESS_ASK_TERMINATE_SERVER = wx.NewId()
        LoginProcess.EVT_LOGINPROCESS_RECONNECT_DIALOG = wx.NewId()
        LoginProcess.EVT_LOGINPROCESS_CONNECT_SERVER = wx.NewId()
        LoginProcess.EVT_LOGINPROCESS_KILL_SERVER = wx.NewId()
        LoginProcess.EVT_LOGINPROCESS_START_SERVER = wx.NewId()
        LoginProcess.EVT_LOGINPROCESS_RESTART_SERVER = wx.NewId()
        LoginProcess.EVT_LOGINPROCESS_NORMAL_TERMINATION = wx.NewId()
        LoginProcess.EVT_LOGINPROCESS_CANCEL = wx.NewId()
        LoginProcess.EVT_LOGINPROCESS_FORWARD_AGENT = wx.NewId()
        LoginProcess.EVT_LOGINPROCESS_START_VIEWER = wx.NewId()
        LoginProcess.EVT_LOGINPROCESS_QUESTION_KILL_SERVER = wx.NewId()
        LoginProcess.EVT_LOGINPROCESS_STAT_RUNNING_JOB = wx.NewId()
        LoginProcess.EVT_LOGINPROCESS_COMPLETE = wx.NewId()
        LoginProcess.EVT_LOGINPROCESS_SHUTDOWN = wx.NewId()
        LoginProcess.EVT_LOGINPROCESS_SHOW_MESSAGE = wx.NewId()
        LoginProcess.EVT_LOGINPROCESS_SHOW_WARNING = wx.NewId()
        LoginProcess.EVT_LOGINPROCESS_SHOW_ERROR = wx.NewId()

        self.notify_window.Bind(self.EVT_CUSTOM_LOGINPROCESS, LoginProcess.loginProcessEvent.cancel)
        self.notify_window.Bind(self.EVT_CUSTOM_LOGINPROCESS, LoginProcess.loginProcessEvent.distributeKey)
        self.notify_window.Bind(self.EVT_CUSTOM_LOGINPROCESS, LoginProcess.loginProcessEvent.checkRunningServer)
        self.notify_window.Bind(self.EVT_CUSTOM_LOGINPROCESS, LoginProcess.loginProcessEvent.checkVNCVer)
        self.notify_window.Bind(self.EVT_CUSTOM_LOGINPROCESS, LoginProcess.loginProcessEvent.showReconnectDialog)
        self.notify_window.Bind(self.EVT_CUSTOM_LOGINPROCESS, LoginProcess.loginProcessEvent.killServer)
        self.notify_window.Bind(self.EVT_CUSTOM_LOGINPROCESS, LoginProcess.loginProcessEvent.startServer)
        self.notify_window.Bind(self.EVT_CUSTOM_LOGINPROCESS, LoginProcess.loginProcessEvent.startTunnel)
        self.notify_window.Bind(self.EVT_CUSTOM_LOGINPROCESS, LoginProcess.loginProcessEvent.connectServer)
        self.notify_window.Bind(self.EVT_CUSTOM_LOGINPROCESS, LoginProcess.loginProcessEvent.forwardAgent)
        self.notify_window.Bind(self.EVT_CUSTOM_LOGINPROCESS, LoginProcess.loginProcessEvent.getVNCPassword)
        self.notify_window.Bind(self.EVT_CUSTOM_LOGINPROCESS, LoginProcess.loginProcessEvent.startViewer)
        self.notify_window.Bind(self.EVT_CUSTOM_LOGINPROCESS, LoginProcess.loginProcessEvent.showKillServerDialog)
        self.notify_window.Bind(self.EVT_CUSTOM_LOGINPROCESS, LoginProcess.loginProcessEvent.shutdown)
        self.notify_window.Bind(self.EVT_CUSTOM_LOGINPROCESS, LoginProcess.loginProcessEvent.normalTermination)
        self.notify_window.Bind(self.EVT_CUSTOM_LOGINPROCESS, LoginProcess.loginProcessEvent.complete)
        self.notify_window.Bind(self.EVT_CUSTOM_LOGINPROCESS, LoginProcess.loginProcessEvent.statRunningJob)
        #self.notify_window.Bind(self.EVT_CUSTOM_LOGINPROCESS, LoginProcess.loginProcessEvent.showMessages)

    def timeRemaining(self):
        # The time fields returned by qstat can either contain HH:MM or --. -- occurs if the job has only just started etc
        # If -- is present, unpacking after split will fail, hence the try: except: combos.
        job=self.job
        if job != None:
            if (job.has_key('reqTime') and job.has_key('elapTime') and job.has_key('state')):
                if (job['state']=='R'):
                    try:
                        (rhours,rmin) = job['reqTime'].split(':')
                    except:
                        return None
                    try:
                        (ehours,emin) = job['elapTime'].split(':')
                    except:
                        ehours=0
                        emin=0
                    return (int(rhours)-int(ehours))*60*60 + (int(rmin)-int(emin))*60
                else:
                    try:
                        (rhours,rmin) = job['reqTime'].split(':')
                    except:
                        return None
                    ehours=0
                    emin=0
                    return (int(rhours)-int(ehours))*60*60 + (int(rmin)-int(emin))*60
            else:
                return None
        else:
            return None
        
    def validateVncJobID(self):
        if (self.vncJobID != None and re.search("^[0-9]+\.\S+$",self.vncJobID)):
            return True
        else:
            return False

    def doLogin(self):
        event=self.loginProcessEvent(LoginProcess.EVT_LOGINPROCESS_CHECK_VNC_VER,self)
        wx.PostEvent(self.notify_window.GetEventHandler(),event)
   
    def cancel(self,error=""):
        if (not self._canceled.isSet()):
            self._canceled.set()
            event=self.loginProcessEvent(LoginProcess.EVT_LOGINPROCESS_CANCEL,self,error)
            wx.PostEvent(self.notify_window.GetEventHandler(),event)
            #logger_error("LoginTasks.cancel error message %s"%error)


    def updateProgressDialog(self, value, message):
        if self.notify_window.progressDialog!=None:
            self.notify_window.progressDialog.Update(value, message)
            self.shouldAbort = self.notify_window.progressDialog.shouldAbort()

    def buildVNCOptionsString(self):
        if sys.platform.startswith("win"):
            optionPrefixCharacter = "/"
        else:
            optionPrefixCharacter = "-"
        vncOptionsString = ""

        # This is necessary to avoid confusion arising from connecting to localhost::[port] after creating SSH tunnel.
        # In this case, the X11 version of TurboVNC assumes that the client and server are the same computer:
        # "Same machine: preferring raw encoding"
        if not sys.platform.startswith("win"):
            if self.jobParams['turboVncFlavour'] == "X11":
                vncOptionsString = "-encodings \"tight copyrect\""
            else:
                vncOptionsString = "-encoding \"Tight\""

        if 'jpeg_compression' in self.notify_window.vncOptions and self.notify_window.vncOptions['jpeg_compression']==False:
            vncOptionsString = vncOptionsString + " " + optionPrefixCharacter + "nojpeg"
        defaultJpegChrominanceSubsampling = "1x"
        if 'jpeg_chrominance_subsampling' in self.notify_window.vncOptions and self.notify_window.vncOptions['jpeg_chrominance_subsampling']!=defaultJpegChrominanceSubsampling:
            vncOptionsString = vncOptionsString + " " + optionPrefixCharacter + "samp " + self.notify_window.vncOptions['jpeg_chrominance_subsampling']
        defaultJpegImageQuality = "95"
        if 'jpeg_image_quality' in self.notify_window.vncOptions and self.notify_window.vncOptions['jpeg_image_quality']!=defaultJpegImageQuality:
            vncOptionsString = vncOptionsString + " " + optionPrefixCharacter + "quality " + self.notify_window.vncOptions['jpeg_image_quality']
        if 'zlib_compression_enabled' in self.notify_window.vncOptions and self.notify_window.vncOptions['zlib_compression_enabled']==True:
            if 'zlib_compression_level' in self.notify_window.vncOptions:
                vncOptionsString = vncOptionsString + " " + optionPrefixCharacter + "compresslevel " + self.notify_window.vncOptions['zlib_compression_level']
        if 'view_only' in self.notify_window.vncOptions and self.notify_window.vncOptions['view_only']==True:
            vncOptionsString = vncOptionsString + " " + optionPrefixCharacter + "viewonly"
        if 'disable_clipboard_transfer' in self.notify_window.vncOptions and self.notify_window.vncOptions['disable_clipboard_transfer']==True:
            if sys.platform.startswith("win"):
                vncOptionsString = vncOptionsString + " /disableclipboard"
            #else:
                #vncOptionsString = vncOptionsString + " -noclipboardsend -noclipboardrecv"
        if sys.platform.startswith("win"):
            if 'scale' in self.notify_window.vncOptions:
                if self.notify_window.vncOptions['scale']=="Auto":
                    vncOptionsString = vncOptionsString + " /fitwindow"
                else:
                    vncOptionsString = vncOptionsString + " /scale " + self.notify_window.vncOptions['scale']
            defaultSpanMode = 'automatic'
            if 'span' in self.notify_window.vncOptions and self.notify_window.vncOptions['span']!=defaultSpanMode:
                vncOptionsString = vncOptionsString + " /span " + self.notify_window.vncOptions['span']
        if 'double_buffering' in self.notify_window.vncOptions and self.notify_window.vncOptions['double_buffering']==False:
            vncOptionsString = vncOptionsString + " " + optionPrefixCharacter + "singlebuffer"
        if 'full_screen_mode' in self.notify_window.vncOptions and self.notify_window.vncOptions['full_screen_mode']==True:
            vncOptionsString = vncOptionsString + " " + optionPrefixCharacter + "fullscreen"
        if 'deiconify_on_remote_bell_event' in self.notify_window.vncOptions and self.notify_window.vncOptions['deiconify_on_remote_bell_event']==False:
            vncOptionsString = vncOptionsString + " " + optionPrefixCharacter + "noraiseonbeep"
        if sys.platform.startswith("win"):
            if 'emulate3' in self.notify_window.vncOptions and self.notify_window.vncOptions['emulate3']==True:
                vncOptionsString = vncOptionsString + " /emulate3"
            if 'swapmouse' in self.notify_window.vncOptions and self.notify_window.vncOptions['swapmouse']==True:
                vncOptionsString = vncOptionsString + " /swapmouse"
        if 'dont_show_remote_cursor' in self.notify_window.vncOptions and self.notify_window.vncOptions['dont_show_remote_cursor']==True:
            vncOptionsString = vncOptionsString + " " + optionPrefixCharacter + "nocursorshape"
        elif 'let_remote_server_deal_with_mouse_cursor' in self.notify_window.vncOptions and self.notify_window.vncOptions['let_remote_server_deal_with_mouse_cursor']==True:
            vncOptionsString = vncOptionsString + " " + optionPrefixCharacter + "x11cursor"
        if 'request_shared_session' in self.notify_window.vncOptions and self.notify_window.vncOptions['request_shared_session']==False:
            vncOptionsString = vncOptionsString + " " + optionPrefixCharacter + "noshared"
        if sys.platform.startswith("win"):
            if 'toolbar' in self.notify_window.vncOptions and self.notify_window.vncOptions['toolbar']==False:
                vncOptionsString = vncOptionsString + " /notoolbar"
            if 'dotcursor' in self.notify_window.vncOptions and self.notify_window.vncOptions['dotcursor']==True:
                vncOptionsString = vncOptionsString + " /dotcursor"
            if 'smalldotcursor' in self.notify_window.vncOptions and self.notify_window.vncOptions['smalldotcursor']==True:
                vncOptionsString = vncOptionsString + " /smalldotcursor"
            if 'normalcursor' in self.notify_window.vncOptions and self.notify_window.vncOptions['normalcursor']==True:
                vncOptionsString = vncOptionsString + " /normalcursor"
            if 'nocursor' in self.notify_window.vncOptions and self.notify_window.vncOptions['nocursor']==True:
                vncOptionsString = vncOptionsString + " /nocursor"
            if 'writelog' in self.notify_window.vncOptions and self.notify_window.vncOptions['writelog']==True:
                if 'loglevel' in self.notify_window.vncOptions and self.notify_window.vncOptions['loglevel']==True:
                    vncOptionsString = vncOptionsString + " /loglevel " + self.notify_window.vncOptions['loglevel']
                if 'logfile' in self.notify_window.vncOptions:
                    vncOptionsString = vncOptionsString + " /logfile \"" + self.notify_window.vncOptions['logfile'] + "\""
        return vncOptionsString