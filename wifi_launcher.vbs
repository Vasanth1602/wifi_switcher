Set UAC = CreateObject("Shell.Application")
UAC.ShellExecute "python.exe", "app.py", "", "runas", 0
