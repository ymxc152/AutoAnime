import io

# ============ 后端:filesystem 端点支持文件列表 ============
p = "autoanime/web/routers/filesystem.py"
s = io.open(p, encoding="utf-8").read()

old = '''class FilesystemListing(BaseModel):
    """GET /api/filesystem 响应（前端 FilesystemListing 同构）。"""

    path: str
    parent: str | None
    directories: list[str]'''
new = '''class FilesystemListing(BaseModel):
    """GET /api/filesystem 响应（前端 FilesystemListing 同构）。

    files 仅在 ``include_files=true`` 时填充（选番抽屉的文件选择模式）；
    默认空列表保持既有响应形状不变。
    """

    path: str
    parent: str | None
    directories: list[str]
    files: list[str] = []'''
assert s.count(old) == 1
s = s.replace(old, new)

old = '''async def browse(path: str = Query(default="")) -> FilesystemListing:
    if path.strip() == "":
        if os.name == "nt":
            return FilesystemListing(path="", parent=None, directories=_list_drives())
        # 非 Windows 平台无盘符概念：兜底返回根目录（列表项与 path 同为 /，
        # parent=null），前端选择器在 POSIX 上同样可用。
        return FilesystemListing(path="/", parent=None, directories=["/"])

    resolved = Path(path).resolve()
    if not resolved.is_dir():
        raise HTTPException(status_code=404, detail="dir_not_found")
    # 根目录（盘符根 / POSIX /）的 parent 等于自身 → null（面包屑到顶）。
    parent: str | None = (
        str(resolved.parent) if resolved.parent != resolved else None
    )
    return FilesystemListing(
        path=str(resolved),
        parent=parent,
        directories=_list_subdirectories(resolved),
    )'''
new = '''async def browse(
    path: str = Query(default=""),
    include_files: bool = Query(default=False),
) -> FilesystemListing:
    if path.strip() == "":
        if os.name == "nt":
            return FilesystemListing(path="", parent=None, directories=_list_drives())
        # 非 Windows 平台无盘符概念：兜底返回根目录（列表项与 path 同为 /，
        # parent=null），前端选择器在 POSIX 上同样可用。
        return FilesystemListing(path="/", parent=None, directories=["/"])

    resolved = Path(path).resolve()
    if not resolved.is_dir():
        raise HTTPException(status_code=404, detail="dir_not_found")
    # 根目录（盘符根 / POSIX /）的 parent 等于自身 → null（面包屑到顶）。
    parent: str | None = (
        str(resolved.parent) if resolved.parent != resolved else None
    )
    return FilesystemListing(
        path=str(resolved),
        parent=parent,
        directories=_list_subdirectories(resolved),
        files=_list_files(resolved) if include_files else [],
    )


def _list_files(resolved: Path) -> list[str]:
    """列文件名（非目录项）；单条 OSError 跳过，scandir 整体失败 → 空表。"""
    files: list[str] = []
    try:
        with os.scandir(resolved) as it:
            for entry in it:
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        files.append(entry.name)
                except OSError:
                    continue
    except OSError:
        return []
    return sorted(files, key=str.casefold)[:_MAX_ENTRIES]'''
assert s.count(old) == 1
s = s.replace(old, new)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("backend filesystem ok")

# ============ 前端 types ============
p = "src/api/types.ts"
s = io.open(p, encoding="utf-8").read()
old = """export interface FilesystemListing {
  /** 当前目录(resolve 后);空串 = Windows 盘符根视图 */
  path: string
  /** 上一级目录;根目录(null)时「上一级」禁用 */
  parent: string | null
  directories: string[]
}"""
new = """export interface FilesystemListing {
  /** 当前目录(resolve 后);空串 = Windows 盘符根视图 */
  path: string
  /** 上一级目录;根目录(null)时「上一级」禁用 */
  parent: string | null
  directories: string[]
  /** 文件名(仅 include_files=true 时填充;文件选择模式用) */
  files: string[]
}"""
assert s.count(old) == 1
s = s.replace(old, new)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("types ok")

# ============ endpoints ============
p = "src/api/endpoints.ts"
s = io.open(p, encoding="utf-8").read()
old = """  filesystem: {
    list: (path?: string) =>
      request<FilesystemListing>('/api/filesystem', { query: { path } }),
  },"""
new = """  filesystem: {
    list: (path?: string, includeFiles?: boolean) =>
      request<FilesystemListing>('/api/filesystem', {
        query: { path, ...(includeFiles ? { include_files: true } : {}) },
      }),
  },"""
assert s.count(old) == 1
s = s.replace(old, new)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("endpoints ok")

# ============ mock handlers ============
p = "src/mocks/handlers.ts"
s = io.open(p, encoding="utf-8").read()
old = """    filesystem: {
      list: (path?: string) =>
        delayed(clone({ ...mockFilesystemListing, ...(path === undefined ? {} : { path }) })),
    },"""
new = """    filesystem: {
      list: (path?: string, includeFiles?: boolean) =>
        delayed(
          clone({
            ...mockFilesystemListing,
            ...(path === undefined ? {} : { path }),
            ...(includeFiles
              ? { files: ['[LoliHouse] 孤独摇滚 - 01 [1080p][简中].mkv', 'README.md'] }
              : {}),
          }),
        ),
    },"""
assert s.count(old) == 1
s = s.replace(old, new)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("mock ok")
