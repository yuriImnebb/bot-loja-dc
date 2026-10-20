# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[('cogs', 'cogs'), ('web', 'web')],
    hiddenimports=['cogs.catalogo', 'cogs.loja', 'cogs.vendas', 'cogs.estoque', 'cogs.admin', 'cogs.master', 'cogs.assinatura', 'cogs.whitelabel', 'cogs.ajuda', 'aiosqlite', 'sqlalchemy.dialects.sqlite.aiosqlite', 'greenlet'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='VendasBot',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
