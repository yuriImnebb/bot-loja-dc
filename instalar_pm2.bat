@echo off
echo Verificando Node.js...
node -v >nul 2>&1
if %errorlevel% neq 0 (
    echo ERRO: Voce precisa instalar o Node.js para usar o PM2.
    echo Baixe em: https://nodejs.org/
    pause
    exit /b
)

echo Instalando PM2 globalmente...
call npm install pm2 -g

echo Instalando inicializador do Windows...
call npm install pm2-windows-startup -g
call pm2-startup install

echo Iniciando o bot no PM2...
:: Usa o python do ambiente virtual para rodar o script main.py
call pm2 start .\.venv\Scripts\python.exe --name "vendas-bot" -- main.py

echo Salvando lista de processos...
call pm2 save

echo.
echo PRONTO! O bot esta sendo gerenciado pelo PM2.
echo Comandos uteis:
echo   pm2 status          - Ver se o bot esta online
echo   pm2 logs vendas-bot - Ver as mensagens do bot em tempo real
echo   pm2 restart vendas-bot - Reiniciar o bot
echo.
pause
