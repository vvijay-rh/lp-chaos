'''Windows VM Stress testing utilities.'''
import logging
from contextlib import contextmanager
from pathlib import Path
from pathlib import PureWindowsPath
import os
from typing import Generator

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
            diskspdDir = self.winVM.EnsurePath(diskspdDir)
            logs.info('Checking if DiskSpd already exists...')
            result = self.vm.ssh.RunPowerShell(
                    f'Get-ChildItem -Path {diskspdDir} -Filter {diskspdExe} -Recurse -File'
                    f'| Select-Object -ExpandProperty FullName'
                )
            if result.success and result.stdout.strip():
                logs.info(f'DiskSpd executable already exists: {result.stdout.strip()}')
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
            archivePath = PureWindowsPath(diskspdDir) / diskspdArchive
            result = self.winVM.ExtractArchive(archivePath)
            if not result:
                logs.error(f'Failed to extract DiskSpd: {result.stderr}')
                return False

            logs.info('Verifying DiskSpd executable...')
            result = self.vm.ssh.RunPowerShell(
                f'Get-ChildItem -Path {diskspdDir} -Filter {diskspdExe} -Recurse -File'
                f'| Select-Object -ExpandProperty FullName'
            )
            if not result.success :
                logs.error(f'DiskSpd verification failed: executable not found at {diskspdDir}')
                return False
            exePath = result.stdout.strip()
            result = self.vm.ssh.RunPowerShell(
                f'Test-Path -Path "{exePath}" -PathType Leaf'
            )
            if not result.success or result.stdout.strip().lower() == 'false':
                logs.error(f'DiskSpd verification failed: executable not found at {diskspdDir}')
                return False
            logs.info('DiskSpd successfully downloaded and verified')
            return True
        except Exception as error:
            logs.error(f'Unexpected error downloading DiskSpd: {error}')
            return False

    def TriggerDiskspdScheduledTask(self, request) -> tuple[bool, str]:
        '''Create and start a scheduled task for DiskSpd.'''
        if not self.DownloadDiskspd():
            return False, "Download Failed"
        diskspdScript = (request.config.rootpath / 'scripts' / self.cfg.diskspdScript).resolve()
        diskspdVMScript = self.winVM.EnsurePath(f"$env:HOMEDRIVE\\{self.cfg.diskspdScript}", isFile=True)
        if not diskspdVMScript:
            return False, "Not able to esnure diskspd script path"
        logs.info(f'Ensured diskspd script path:{diskspdVMScript}')
        if not self.winVM.SendFile(
            sourceFilePath=diskspdScript,
            destFilePath=diskspdVMScript,
            verify=True
        ):
            return False, "Trigger script upload failed"

        result = self.winVM.ScheduledTaskTrigger(
            taskName=self.cfg.taskName, 
            diskspdScript=diskspdVMScript,
            args=self.cfg.taskArgs
        )
        return result.success, result.stderr if not result.success else ''
        
    def KillDiskspd(self) -> bool:
        '''Get the parent Id of diskspd proces and kill the process tree.'''
        if not self.winVM.IsProcRunning(procName=self.cfg.diskspdExe):
            logs.error(f'Process diskspd is not found running')
            return False
        ppid = self.winVM.GetPpid(procName=self.cfg.diskspdExe)
        logs.info(f'DiskSpd PPID: {ppid}')
        return self.winVM.KillPidTree(pid=ppid)
        
    def DeleteDiskSpdTask(self) -> bool:
        '''Delete the DiskSpd scheduled task if it exists.'''
        return self.winVM.DeleteTask(taskName=self.cfg.taskName)

    @contextmanager
    def DiskSpdContext(self) -> Generator[WinStress, None, None]:
        '''
        Context manager for DiskSpd lifecycle.

        Automatically triggers DiskSpd and cleans up on exit.

        :param timeout_seconds: How long to run DiskSpd before cleanup.
        :yield: Self for use in with block.
        :raises RuntimeError: If DiskSpd trigger fails.
        '''
        try:
            success, errorMsg = self.TriggerDiskspdScheduledTask()
            if not success:
                raise RuntimeError(f'Failed to trigger DiskSpd: {errorMsg}')

            logs.info(f'DiskSpd running')
            yield self

        finally:
            logs.info('Starting DiskSpd cleanup...')
            cleanupErrors = []

        try:
            if self.KillDiskspd():
                logs.info('DiskSpd process killed successfully')
            else:
                logs.warning('Failed to kill DiskSpd process')
                cleanupErrors.append('Kill process failed')
        except Exception as error:
            logs.error(f'Error killing DiskSpd: {error}')
            cleanupErrors.append(str(error))

        try:
            if self.DeleteDiskSpdTask():
                logs.info('DiskSpd task deleted successfully')
            else:
                logs.warning('Failed to delete DiskSpd task')
                cleanupErrors.append('Delete task failed')
        except Exception as error:
            logs.error(f'Error deleting task: {error}')
            cleanupErrors.append(str(error))

        if cleanupErrors:
            logs.warning(f'Cleanup completed with errors: {cleanupErrors}')