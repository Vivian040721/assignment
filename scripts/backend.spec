from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, copy_metadata

root = Path(SPECPATH).parent
datas = collect_data_files('paper_agent', includes=['fixtures/*.json', 'fixtures/*.md']) + collect_data_files('pymupdf4llm') + collect_data_files('pymupdf')
for distribution in ('mcp','mcp-types','pydantic','pymupdf','pymupdf4llm','pymupdf-layout'):
    datas += copy_metadata(distribution)
a = Analysis([str(root/'scripts/backend_entry.py')], pathex=[str(root/'src')],
    binaries=collect_dynamic_libs('pymupdf'), datas=datas,
    hiddenimports=['paper_agent.service_daemon','paper_agent.service_control','mcp.client.streamable_http',
                   'uvicorn.logging','uvicorn.lifespan.on','uvicorn.loops.asyncio',
                   'uvicorn.protocols.http.h11_impl','pymupdf4llm.helpers.pymupdf_rag'],
    excludes=['PySide6','shiboken6','watchfiles','tkinter','pytest','IPython','matplotlib',
              'torch','tensorflow','sentence_transformers'], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz,a.scripts,[],exclude_binaries=True,name='PaperAgent-service',console=True,
          strip=False,upx=False,debug=False)
collection = COLLECT(exe,a.binaries,a.datas,name='PaperAgent-service',strip=False,upx=False)
