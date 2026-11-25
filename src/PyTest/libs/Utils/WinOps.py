'''Windows VM Stress testing utilities.'''
import logging

from libs.OpenShift.LP.Virt.VM.VM import VM
from libs.Utils.CmdExec import CmdRes

logs = logging.getLogger(__name__)

class WinOps:
    '''Windows VM operations: file management, process monitoring, scheduled tasks.'''
    
    def __init__(self, vm: VM) -> None:
        '''
        :param vm: VM object for SSH command execution.
        '''
        self.vm = vm

    def DownloadUtility(self, url: str, destDir: str) -> bool:
        '''
        Download the utility.
        :return: True downloaded is successfull; False otherwise.
        '''    
        try:
            logs.info('Checking if directory already exists...')
            result = self.vm.ssh.RunPowerShell(
                f'Test-Path -Path "{destDir}" -PathType Container'
            )
            if result.success and result.stdout.strip().lower() != 'true':
                logs.info(f'Directory not found, creating: {destDir}')
                result = self.vm.ssh.RunPowerShell(
                        f'New-Item -Path "{destDir}" -ItemType Directory -Force'
                    )
                if not result.success:
                    logs.error(f'Failed to create directory: {result.stderr}')
                    return False
            result = self.vm.ssh.RunPowerShell(
                f'$destDir = "{destDir}"; '
                f'$url = "{url}"; '
                '$filename = Split-Path -Leaf $url; '
                '$outFile = Join-Path $destDir $filename; '
                'try { '
                '  Invoke-WebRequest '
                '  -Uri $url '
                '  -OutFile $outFile -TimeoutSec 300 -ErrorAction Stop; '
                '  Write-Output "Downloaded: $(Test-Path -Path $outFile -PathType Leaf)"; '
                '} catch { '
                '  Write-Error "Download failed: $_"; '
                '  exit 1; '
                '}'
            )
            if result.success and 'True' not in result.stdout:
                logs.error(f'Download verification failed: file not found after download')
                return False
            result = self.vm.ssh.RunPowerShell(
                f'$filename = Split-Path -Leaf "{url}"; '
                f'$filePath = Join-Path "{destDir}" $filename; '
                'Test-Path -Path $filePath -PathType Leaf'
            )
            if not result.success or result.stdout.strip().lower() != 'true':
                logs.error(f'Download verification failed: file not found in {destDir}')
                return False
        except Exception as error:
                logs.error(f'Unexpected error downloading DiskSpd: {error}')
                return False
        logs.info(f'Successfully downloaded to {destDir}')
        return True
    
    def ExtractArchive(self, archivePath: str) -> bool:
        '''
        Extract ZIP archive to its parent directory.

        :param archivePath: Path to ZIP archive.
        :return:            True if extraction successful; False otherwise.
        '''
        try:
            logs.info(f'Checking if {archivePath} exists...')
            result = self.vm.ssh.RunPowerShell(
                    f'Test-Path -Path "{archivePath}" -PathType Leaf'
            )
            if result.success and result.stdout.strip().lower() != 'true':
                logs.error(f'Failed to extract {archivePath}: {result.stderr}')
                return False
            logs.info('Extracting {archivePath}...')
            result = self.vm.ssh.RunPowerShell(
                f'try {{ '
                f'  $extractPath = Split-Path "{archivePath}"; '
                f'  Expand-Archive -Path "{archivePath}" -DestinationPath $extractPath -Force -ErrorAction Stop; '
                f'  Write-Output "Extracted"; '
                f'}} catch {{ '
                f'  Write-Error "Extraction failed: $_"; '
                f'  exit 1; '
                f'}}'
            )
            if not result.success:
                logs.error(f'Failed to extract {archivePath}: {result.stderr}')
                return False
            logs.info('Archive extracted successfully')
            return True
        except Exception as error:
            logs.error(f'Unexpected error during extraction: {error}')
            return False

    def ScheduledTaskTrigger(self, taskName: str, args: str) -> CmdRes:
        '''
        Create and immediately start a scheduled task.

        :param taskName:    Name for the scheduled task.
        :param args:        PowerShell arguments.
        :return:            CmdRes from task creation.
        :raises RuntimeError: If task creation or start fails.
        '''
        logs.info(f'Creating scheduled task: {taskName}, args: {args}')
        result = self.vm.ssh.RunPowerShell(
                f'$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date); '
                f'$action = New-ScheduledTaskAction -Execute "powershell.exe" '
                f'-Argument "{args}"; '
                f'Register-ScheduledTask -TaskName "{taskName}" -Trigger $trigger -Action $action -Force; '
                f'Start-ScheduledTask -TaskName "{taskName}"'
        )
        if not result.success:
            raise RuntimeError(f'Failed to trigger scheduled task {taskName}: {result.stderr}')
        return result

    def GetPid(self, procName: str) -> str:
        '''
        Get process ID by process name.

        :param procName:    Process name (e.g., 'diskspd.exe').
        :return:            Process ID as string.
        :raises RuntimeError: If process not found.
        '''
        result = self.vm.ssh.RunPowerShell(
            f'(Get-CimInstance Win32_Process -Filter "Name = \'{procName}\'").ProcessId'
        )
        if not result.success or not result.stdout.strip():
            raise RuntimeError(f'{procName} process not found')
        return result.stdout.strip()
        
    def GetPpid(self, procName: str) -> str:
        '''
        Get parent process ID by process name.

        :param procName:    Process name.
        :return:            Parent process ID as string.
        :raises RuntimeError: If process not found.
        '''
        result = self.vm.ssh.RunPowerShell(
            f'(Get-CimInstance Win32_Process -Filter "Name = \'{procName}\'").ParentProcessId'
        )
        if not result.success or not result.stdout.strip():
            raise RuntimeError(f'{procName} process not found')
        return result.stdout.strip()
        
    def KillPidTree(self, pid: int) -> bool:
        '''
        Kill process and its children.

        :param pid:     Process ID to kill.
        :return:        True if successful; False otherwise.
        '''
        try:
            logs.info(f'Killing process tree: PID {pid}')
            result = self.vm.ssh.RunPowerShell(f'taskkill /PID {pid} /T /F')
            if not result.success:
                logs.error(f'Failed to kill process tree: {result.stderr.strip()}')
                return False
            logs.info(f'Process tree {pid} terminated')
            return True
        except Exception as error:
            logs.error(f'Error killing process: {error}')
            return False

    def TaskExists(self, taskName: str) -> bool:
        '''
        Check if scheduled task exists.

        :param taskName:    Task name to check.
        :return:            True if task exists; False otherwise.
        '''
        result = self.vm.ssh.RunPowerShell(
            f'Get-ScheduledTask -TaskName "{taskName}" -ErrorAction SilentlyContinue'
        )
        return result.success and bool(result.stdout.strip())
        
    def DeleteTask(self, taskName: str) -> bool:
        '''
        Delete scheduled task if it exists.

        :param taskName:    Task name to delete.
        :return:            True if deletion successful or task didn't exist; False on error.
        '''
        if not self.TaskExists(taskName):
            logs.info(f'Task does not exist: {taskName}')
            return True
        logs.info(f'Deleting task: {taskName}')
        result = self.vm.ssh.RunPowerShell(
            f'Stop-ScheduledTask -TaskName "{taskName}" -ErrorAction SilentlyContinue; '
            f'Unregister-ScheduledTask -TaskName "{taskName}" -Confirm:$false'
        )
        if not result.success:
            logs.error(f'Failed to delete task {taskName}: {result.stderr}')
            return False
        if self.TaskExists(taskName):
            logs.error(f'Task:{taskName} still exists after deletion')
            return False
        logs.info(f'Task deleted successfully {taskName}')
        return True

    def IsProcRunning(self, procName: str) -> bool:
        '''
        Check if process is currently running.

        :param procName:    Process name.
        :return:            True if running; False otherwise.
        '''
        try:
            result = self.vm.ssh.RunPowerShell(
                f'Get-Process -Name {procName} -ErrorAction SilentlyContinue | Select-Object -First 1'
            )
            return result.success and bool(result.stdout.strip())
        except Exception as error:
            logs.error(f'Failed to check process status {procName}: {error}')
            return False