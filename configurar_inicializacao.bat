@echo off
set "targetDir=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup"
set "exePath=%~dp0dist\VendasBot.exe"
set "envPath=%~dp0.env"
set "dataPath=%~dp0data"

echo Configurando o bot para iniciar com o Windows...

if not exist "%exePath%" (
    echo ERRO: O arquivo dist\VendasBot.exe nao foi encontrado. Gere o .exe primeiro.
    pause
    exit /b
)

:: Copia o .env e a pasta data para a pasta dist (se nao existirem la)
if not exist "%~dp0dist\.env" copy "%envPath%" "%~dp0dist\.env"
if not exist "%~dp0dist\data" xcopy "%dataPath%" "%~dp0dist\data" /I /E /Y

:: Cria o atalho na pasta Inicializar
set "shortcutName=VendasBot.lnk"
powershell -Command "$s=(New-Object -COM WScript.Shell).CreateShortcut('%targetDir%\%shortcutName%');$s.TargetPath='%exePath%';$s.WorkingDirectory='%~dp0dist';$s.Save()"

echo.
echo PRONTO! O bot agora vai ligar sozinho e em segundo plano toda vez que voce ligar o PC.
echo Para desligar, use o Gerenciador de Tarefas e finalize o processo "VendasBot.exe".
echo.
pause
