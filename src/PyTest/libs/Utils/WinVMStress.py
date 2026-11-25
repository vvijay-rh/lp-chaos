'''Windows VM Stress testing utilities.'''
import logging
from contextlib import contextmanager
from pathlib import Path
import os

from libs.OpenShift.LP.Virt.VM.VM import VM
from libs.OpenShift.OCP.CLI.OC import OCcli
from libs.OpenShift.LP.Virt.CLI.VirtCtl import VirtCtlCLI
from libs.Utils.CmdExec import CmdRes
from libs.CustomTypes import NamedDict
from libs.Utils.WinOps import WinOps

logs = logging.getLogger(__name__)

class WinStress:
    '''Windows VM stress testing via scheduled tasks and process monitoring.'''

    def __init__(
        self, 
        name: str, 
        ns: str, 
        oc: OCcli, 
        vc: VirtCtlCLI, 
        fxtCfg: NamedDict,
        sshKeyPath: str | None = None,
    ) -> None:
        '''
        :param name:        VM name identifier.
        :param ns:          Kubernetes namespace.
        :param oc:          OCcli instance.
        :param vc:          VirtCtlCLI instance.
        :param fxtCfg:      Fixture configuration.
        :param sshKeyPath:  Path to SSH private key (default: ~/.ssh/openshift-qe.pem).
        '''
        if sshKeyPath is None:
            sshKeyPath = str(Path.home() / '.ssh' / 'openshift-qe.pem')
        else:
            sshKeyPath = os.path.expanduser(sshKeyPath)

        self.vm = VM(
            name=name,
            ns=ns,
            oc=oc,
            virtCtl=vc,
            **(fxtCfg.fxtInf.gstCfg.usrCrd + {'sshKey': sshKeyPath}),
        )
        self.winVM = WinOps(self.vm)
        self.cfg = NamedDict(
            **(fxtCfg.testPars.get('WinStress', {}) or {})
        )

    def DownloadDiskspd(self) -> bool:
        '''
        Download and extract DiskSpd, verifying successful installation.

        :return: True if DiskSpd is downloaded and exists; False otherwise.
        '''
        diskspdDir = self.cfg.diskspdDir
        diskspdExe = self.cfg.diskspdExe
        diskspdArchive = self.cfg.diskspdArchive
        diskspdUrl = self.cfg.diskspdUrl
        try:
            logs.info('Checking if DiskSpd already exists...')
            result = self.vm.ssh.RunPowerShell(
                f'Test-Path -Path "{diskspdExe}" -PathType Leaf'
            )
            if result.success and result.stdout.strip().lower() == 'true':
                logs.info('DiskSpd already exists')
                return True

            logs.info('DiskSpd not found, downloading...')
            result = self.winVM.DownloadUtility(
                diskspdUrl,
                diskspdDir,
            )
            if not result:
                logs.error('Failed to download DiskSpd')
                return False

            logs.info('Extracting DiskSpd...')
            result = self.winVM.ExtractArchive(diskspdArchive)
            if not result:
                logs.error(f'Failed to extract DiskSpd: {result.stderr}')
                return False

            logs.info('Verifying DiskSpd executable...')
            result = self.vm.ssh.RunPowerShell(
                f'Test-Path -Path "{diskspdExe}" -PathType Leaf'
            )
            if not result.success or result.stdout.strip().lower() == 'false':
                logs.error(f'DiskSpd verification failed: executable not found at {diskspdExe}')
                return False

            logs.info('DiskSpd successfully downloaded and verified')
            return True
        except Exception as error:
            logs.error(f'Unexpected error downloading DiskSpd: {error}')
            return False

    def TriggerDiskspdScheduledTask(self) -> tuple[bool, str]:
        '''Create and start a scheduled task for DiskSpd.'''
        if not self.DownloadDiskspd():
            return False, "Download Failed"
        result = self.winVM.ScheduledTaskTrigger(
            taskName=self.cfg.taskName, 
            args=self.cfg.taskArgs
        )
        return result.success, result.stderr if not result.success else ''
        
    def KillDiskspd(self) -> bool:
        '''Get the parent Id of diskspd proces and kill the process tree.'''
        if not self.winVM.IsProcRunning(procName="diskspd.exe"):
            logs.error(f'Process diskspd is not found running')
            return False
        ppid = self.winVM.GetPpid(procName="diskspd.exe")
        logs.info(f'DiskSpd PPID: {ppid}')
        return self.winVM.KillPidTree(pid=ppid)
        
    def DeleteDiskSpdTask(self) -> bool:
        '''Delete the DiskSpd scheduled task if it exists.'''
        return self.winVM.DeleteTask(taskName=self.cfg.taskName)

    @contextmanager
    def DiskSpdContext(self):
        '''Context manager for DiskSpd lifecycle.'''
        try:
            if not self.TriggerDiskspdScheduledTask():
                raise RuntimeError('Failed to trigger DiskSpd')
            yield self
        finally:
            try:
                self.KillDiskspd()
                self.DeleteDiskSpdTask()
            except Exception as e:
                logs.error(f'Cleanup failed: {e}')