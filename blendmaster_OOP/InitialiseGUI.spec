# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['C:\\BlendMaster\\blendmaster_OOP\\GUI\\InitialiseGUI.py'],
    pathex=['C:\\BlendMaster\\blendmaster_OOP'],
    binaries=[],
    datas=[('C:\\BlendMaster\\blendmaster_OOP\\requirements.txt', '.'), ('C:\\BlendMaster\\blendmaster_OOP\\blendmaster.db', '.'), ('C:\\BlendMaster\\blendmaster_OOP\\setup\\sql\\opf_daily_reconciliation.sql', 'setup\\sql')],
    hiddenimports=['reportlab.graphics.shapes', 'reportlab.platypus'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PySide6'],
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
    name='InitialiseGUI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['C:\\BlendMaster\\blendmaster_OOP\\resources\\icon.ico'],
)
