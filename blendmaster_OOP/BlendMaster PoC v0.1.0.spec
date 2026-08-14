# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['C:\\BlendMaster\\blendmaster_OOP\\GUI\\InitialiseGUI.py'],
    pathex=['C:\\BlendMaster\\blendmaster_OOP'],
    binaries=[('C:\\Users\\armin.sabet\\AppData\\Local\\Programs\\Python\\Python312\\Lib\\site-packages\\pulp\\solverdir\\cbc\\win\\i64\\cbc.exe', 'pulp\\solverdir\\cbc\\win\\i64')],
    datas=[('C:\\BlendMaster\\blendmaster_OOP\\requirements.txt', '.'), ('C:\\BlendMaster\\blendmaster_OOP\\blendmaster.db', '.'), ('C:\\BlendMaster\\blendmaster_OOP\\setup\\sql\\opf_daily_reconciliation.sql', 'setup\\sql'), ('C:\\BlendMaster\\blendmaster_OOP\\resources\\expit_excavator.png', 'resources'), ('C:\\BlendMaster\\blendmaster_OOP\\resources\\splash_v2.png', 'resources'), ('C:\\BlendMaster\\blendmaster_OOP\\resources\\background_v4.PNG', 'resources'), ('C:\\BlendMaster\\blendmaster_OOP\\resources\\background_v3.PNG', 'resources'), ('C:\\BlendMaster\\blendmaster_OOP\\resources\\background_v2.PNG', 'resources'), ('C:\\BlendMaster\\blendmaster_OOP\\resources\\background.PNG', 'resources'), ('C:\\BlendMaster\\blendmaster_OOP\\resources\\icon_v2.png', 'resources'), ('C:\\BlendMaster\\blendmaster_OOP\\resources\\icon_2_v2.ico', 'resources'), ('C:\\BlendMaster\\blendmaster_OOP\\resources\\icon_2.ico', 'resources'), ('C:\\BlendMaster\\blendmaster_OOP\\GUI\\AgentBridgeServer.py', 'GUI'), ('C:\\Users\\armin.sabet\\AppData\\Local\\Programs\\Python\\Python312\\Lib\\site-packages\\pulp\\solverdir\\cbc\\win\\i64\\coin-license.txt', 'pulp\\solverdir\\cbc\\win\\i64')],
    hiddenimports=['GUI.AgentBridgeServer', 'reportlab.graphics.shapes', 'reportlab.platypus'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['PySide6'],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)
splash = Splash(
    'C:\\BlendMaster\\blendmaster_OOP\\resources\\splash_v2.png',
    binaries=a.binaries,
    datas=a.datas,
    text_pos=None,
    text_size=12,
    minify_script=True,
    always_on_top=True,
)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    splash,
    splash.binaries,
    [],
    name='BlendMaster PoC v0.1.0',
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
    icon='C:\\BlendMaster\\blendmaster_OOP\\resources\\icon_2_v2.ico',
)
