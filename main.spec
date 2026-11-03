a = Analysis(['main.py'], optimize=2)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, a.binaries, a.datas, [],
    name='pix', console=True, strip=True, upx=False,
)
