"""一次性脚本（v3 plan Task 1）：把 v2 的三处文档移入 docs/archive/v2/ 并改写链接。

移动三项（docs/design-system.md 不动，spec §15.1 保留的组件层仍以它为标准）：
docs/roadmap/、docs/features/、docs/architecture.md。

对每个受 git 跟踪的 `*.md` 做两类改写，都只动路径、不动其他文字：

- Markdown 链接 `](目标#锚点)`（跳过 http、mailto、纯锚点）：按文件移动前的路径解析出目标，
  把目标和文件自身都按 MOVES 映射，再按文件移动后的路径重新算相对路径写回。
  被移动文件之间的互链（相对结构不变）因此保持原样。
- YAML frontmatter 的 `sources: [..]` 行：把仓库根相对路径按 MOVES 映射（概念卡在那里引用
  docs/features/*.md，加载器要求每个 source 存在；此改写经编排者批准，仅限 sources 行、不动正文、
  不加卡版本）。

幂等：第二次运行没有移动也没有任何改写。
"""
from __future__ import annotations

import posixpath
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

MOVES: dict[str, str] = {
    'docs/roadmap/': 'docs/archive/v2/roadmap/',
    'docs/features/': 'docs/archive/v2/features/',
    'docs/architecture.md': 'docs/archive/v2/architecture.md',
}

LINK = re.compile(r'(\[[^\]]*\]\()([^)\s]+)((?:\s+"[^"]*")?\))')   # [文字](目标 "标题")，组 2 是目标
SKIP = ('http://', 'https://', 'mailto:', '#')                   # 外部链接与纯锚点不动
SOURCES_LINE = re.compile(r'^sources:\s*\[(.*)\]\s*$')           # frontmatter 的 sources 行
ITEM = re.compile(r'[^,\[\]\s]+')                                # sources 列表里的一个裸路径


def map_path(path: str) -> str:
    """把仓库根相对路径按 MOVES 映射；目录项映射其下所有文件，未命中原样返回。"""
    for src, dst in MOVES.items():
        if src.endswith('/'):
            if path == src.rstrip('/') or path.startswith(src):
                return dst + path[len(src):]
        elif path == src:
            return dst
    return path


def unmap_path(path: str) -> str:
    """map_path 的逆映射：由移动后的路径还原移动前的路径。"""
    for src, dst in MOVES.items():
        if dst.endswith('/'):
            if path == dst.rstrip('/') or path.startswith(dst):
                return src + path[len(dst):]
        elif path == dst:
            return src
    return path


def rewrite_links(text: str, old_dir: str, new_dir: str) -> tuple[str, int]:
    """改写 text 里的 Markdown 链接目标；old_dir / new_dir 是本文件移动前 / 后的目录（仓库根相对）。"""
    count = 0

    def resolve(target: str) -> str | None:
        """把链接目标解析成仓库根相对路径：先按移动前布局（命中被移动的树、或文件仍在），
        再按移动后的布局（幂等：已写回的链接指向现存文件）。都解析不出则返回 None。"""
        for base in (old_dir, new_dir):
            resolved = posixpath.normpath(posixpath.join(base, target))
            if resolved.startswith('..'):
                continue                                   # 这个布局下指向仓库外
            if map_path(resolved) != resolved or (ROOT / resolved).exists():
                return resolved
        return None

    def new_target(whole: str) -> str:
        target, _, anchor = whole.partition('#')
        if not target or target.startswith(SKIP):
            return whole                                  # 纯锚点、外部链接
        resolved = resolve(target)
        if resolved is None:
            return whole
        mapped = map_path(resolved)
        if mapped == resolved and old_dir == new_dir:
            return whole                                  # 目标与文件都没动
        relative = posixpath.relpath(mapped, new_dir)     # 文件移动后重算相对路径（互链往往不变）
        return f'{relative}#{anchor}' if anchor else relative

    def sub(match: re.Match) -> str:
        nonlocal count
        old, new = match.group(2), new_target(match.group(2))
        if new == old:
            return match.group(0)
        count += 1                                   # 只统计真正写回的变化（互链不变的不算）
        return f'{match.group(1)}{new}{match.group(3)}'   # 按位置只换目标，链接文字不动

    return LINK.sub(sub, text), count


def rewrite_sources(text: str) -> tuple[str, int]:
    """把 frontmatter `sources: [..]` 行里命中 MOVES 的仓库根相对路径映射到新位置；其余文字不动。"""
    lines = text.split('\n')
    if not lines or lines[0].strip() != '---':
        return text, 0
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == '---')
    except StopIteration:
        return text, 0
    count = 0
    for i in range(1, end):
        match = SOURCES_LINE.match(lines[i])
        if not match or any(quote in match.group(1) for quote in '"\''):
            continue

        def sub(item: re.Match) -> str:
            nonlocal count
            new = map_path(item.group(0))
            if new != item.group(0):
                count += 1
            return new

        lines[i] = f'sources: [{ITEM.sub(sub, match.group(1))}]'
    return ('\n'.join(lines), count) if count else (text, 0)


def move_docs() -> None:
    for src, dst in MOVES.items():
        source, target = ROOT / src, ROOT / dst
        if source.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            subprocess.run(['git', 'mv', src.rstrip('/'), dst.rstrip('/')], cwd=ROOT, check=True)
        elif not target.exists():
            raise SystemExit(f'{src} 与 {dst} 都不存在，仓库状态与预期不符')


def tracked_markdown() -> list[str]:
    out = subprocess.run(['git', 'ls-files', '-z', '*.md'], cwd=ROOT, check=True, capture_output=True).stdout
    return [name for name in out.decode('utf-8').split('\0') if name]


def main() -> None:
    move_docs()
    files = links_total = sources_total = 0
    for name in tracked_markdown():
        path = ROOT / name
        if not path.is_file():
            continue
        text = path.read_text(encoding='utf-8')
        text, links = rewrite_links(text, posixpath.dirname(unmap_path(name)), posixpath.dirname(name))
        text, sources = rewrite_sources(text)
        if links or sources:
            path.write_text(text, encoding='utf-8', newline='')
            files += 1
            links_total += links
            sources_total += sources
            parts = [f'{name}: ']
            if links:
                parts.append(f'链接 {links} 处')
            if sources:
                parts.append(f'sources {sources} 处')
            print('，'.join(parts))
    print(f'共 {files} 个文件，链接 {links_total} 处，sources {sources_total} 处')


if __name__ == '__main__':
    main()
