'''Windows VM Stress testing utilities.'''
import logging
from pathlib import Path
from pathlib import PureWindowsPath

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
                '} catch { '
                '  Write-Error "Download failed: $_"; '
                '  exit 1; '
                '}'
            )
            if not result.success :
                logs.error(f'Download failed: {result.stderr}')
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
                logs.error(f'File not found, failed to extract {archivePath}: {result.stderr}')
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

    def ScheduledTaskTrigger(self, taskName: str, diskspdScript: str, args: str) -> CmdRes:
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
                f'-Argument "-NoProfile -File {diskspdScript} {args}"; '
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
            name = Path(procName).stem
            result = self.vm.ssh.RunPowerShell(
                f'Get-Process -Name {name} -ErrorAction SilentlyContinue | Select-Object -First 1'
            )
            return result.success and bool(result.stdout.strip())
        except Exception as error:
            logs.error(f'Failed to check process status {procName}: {error}')
            return False

    def ResolvePath(self, destPath: str) -> str | None:
        # Expand environment variables in destination path
        logs.info('Resolving the destination path...')
        result = self.vm.ssh.RunPowerShell(
            f'Write-Output ([Environment]::ExpandEnvironmentVariables("{destPath}"))'
        )
        if not result.success or not result.stdout.strip():
            logs.error(f'Failed to expand path: {destPath}')
            return None
        return result.stdout.strip()

    def EnsurePath(self, destPath: str, isFile: bool = False) -> str | None:
        '''
        Expand environment variables, verify path exists, and create if needed.

        :param destPath:    Path with potential $env: variables (file or folder).
        :param isFile:      If True, treat as file path and create parent directory.
                            If False, treat as folder path and create it.
        :return:            Expanded path if successful; None if failed.
        '''
        try:
            logs.info(f'Resolving {"file" if isFile else "folder"} path: {destPath}')

            expandedPath = self.ResolvePath(destPath)
            if not expandedPath :
                return None

            logs.info(f'Expanded: {destPath} -> {expandedPath}')

            if isFile:
                # For file path, check/create parent directory
                checkPath = str(PureWindowsPath(expandedPath).parent)
                #checkPath = f'Split-Path -Path "{expandedPath}" -Parent'
                pathType = 'Directory (parent of file)'
            else:
                # For folder path, check/create the path itself
                checkPath = f'"{expandedPath}"'
                pathType = 'Directory'

            result = self.vm.ssh.RunPowerShell(
                f'Test-Path -Path {checkPath} -PathType Container'
            )

            if result.success and result.stdout.strip().lower() == 'true':
                logs.info(f'{pathType} already exists: {expandedPath}')
                return expandedPath

            logs.info(f'{pathType} does not exist, creating: {expandedPath}')
            if isFile:
                # Create parent directory for file
                result = self.vm.ssh.RunPowerShell(
                    f'try {{ '
                    f'  $parentDir = Split-Path -Path "{expandedPath}" -Parent; '
                    f'  New-Item -Path $parentDir -ItemType Directory -Force | Out-Null; '
                    f'  Write-Output "Created"; '
                    f'}} catch {{ '
                    f'  Write-Error "Creation failed: $_"; exit 1; '
                    f'}}'
                )
            else:
                # Create directory
                result = self.vm.ssh.RunPowerShell(
                    f'try {{ '
                    f'  New-Item -Path "{expandedPath}" -ItemType Directory -Force | Out-Null; '
                    f'  Write-Output "Created"; '
                    f'}} catch {{ '
                    f'  Write-Error "Creation failed: $_"; exit 1; '
                    f'}}'
                )

            if not result.success or 'Created' not in result.stdout:
                logs.error(f'Failed to create {pathType}: {expandedPath}')
                return None

            logs.info(f'{pathType} created successfully: {expandedPath}')
            return expandedPath

        except Exception as error:
            logs.error(f'Unexpected error resolving path: {error}')
            return None

    def SendFile(self, sourceFilePath: str, destFilePath: str, verify: bool = True) -> bool:
        '''
        Send file from host to VM destination folder.

        :param sourceFilePath:  Path to source file on host (absolute or expandable).
        :param destPath:        Destination file path on VM.
        :param verify:          Verify file exists on VM after transfer.
        :return:                True if transfer successful; False otherwise.
        '''
        import os
        from pathlib import Path
        try:
            # Expand and validate source file
            sourcePath = Path(os.path.expanduser(sourceFilePath)).resolve()
            if not sourcePath.is_file():
                logs.error(f'Source file not found: {sourcePath}')
                return False

            fileSize = sourcePath.stat().st_size
            logs.info(f'Sending {sourcePath.name} ({fileSize} bytes) to VM: {destFilePath}')

            # Ensure destination directory exists on VM
            resolvedFilePath = self.EnsurePath(destPath=destFilePath, isFile=True)
            if not resolvedFilePath:
                return False

            # Send file using VM's SSH connection
            logs.info(f'Transferring file to: {resolvedFilePath}')
            result = self.vm.ssh.Send(str(sourcePath), resolvedFilePath)

            if not result:
                logs.error(f'Failed to send file: {sourcePath.name}')
                return False

            logs.info(f'File sent successfully: {resolvedFilePath}')

            # Verify file on VM
            if verify:
                logs.info(f'Verifying file on VM: {resolvedFilePath}')
                result = self.vm.ssh.RunPowerShell(
                    f'Test-Path -Path "{resolvedFilePath}" -PathType Leaf'
                )
                if not result.success or result.stdout.strip().lower() != 'true':
                    logs.error(f'File verification failed: {resolvedFilePath}')
                    return False

                # Check file size matches
                result = self.vm.ssh.RunPowerShell(
                    f'(Get-Item "{resolvedFilePath}").Length'
                )
                if result.success:
                    remoteSize = int(result.stdout.strip())
                    if remoteSize != fileSize:
                        logs.error(
                            f'File size mismatch: local={fileSize}, remote={remoteSize}'
                        )
                        return False

                logs.info(f'File verified on VM: {resolvedFilePath}')
            return True
        except Exception as error:
            logs.error(f'Unexpected error sending file: {error}')
            return False