from __future__ import annotations

import queue
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


@dataclass
class ExtractionStats:
    moved: int = 0
    renamed: int = 0
    deleted_folders: int = 0
    skipped: int = 0


class PngExtractor:
    """负责文件扫描、移动和清理，不处理 GUI。"""

    def __init__(
        self,
        target_dir: Path,
        delete_empty_folders: bool = True,
        dry_run: bool = False,
        log_callback=None,
    ):
        self.target_dir = target_dir.resolve()
        self.delete_empty_folders = delete_empty_folders
        self.dry_run = dry_run
        self.log_callback = log_callback or (lambda message: None)

    def log(self, message: str) -> None:
        self.log_callback(message)

    def find_png_files(self) -> list[Path]:
        """递归查找 PNG 文件，并跳过目标文件夹根目录中的文件。"""
        return sorted(
            (
                path
                for path in self.target_dir.rglob("*")
                if path.is_file()
                and path.suffix.lower() == ".png"
                and path.parent != self.target_dir
            ),
            key=lambda path: str(path).lower(),
        )

    def get_available_destination(
        self,
        source: Path,
        reserved_names: set[str],
    ) -> tuple[Path, bool]:
        """
        获取不覆盖现有文件的目标路径。
        reserved_names 用于避免本次运行中多个同名文件产生冲突。
        """
        destination = self.target_dir / source.name

        if (
            not destination.exists()
            and destination.name.casefold() not in reserved_names
        ):
            return destination, False

        stem = source.stem
        suffix = source.suffix
        number = 1

        while True:
            candidate = self.target_dir / f"{stem}_{number}{suffix}"

            if (
                not candidate.exists()
                and candidate.name.casefold() not in reserved_names
            ):
                return candidate, True

            number += 1

    def remove_empty_folders(self, stats: ExtractionStats) -> None:
        """从最深层开始删除空子文件夹。"""
        folders = sorted(
            (
                path
                for path in self.target_dir.rglob("*")
                if path.is_dir() and path != self.target_dir
            ),
            key=lambda path: len(path.parts),
            reverse=True,
        )

        for folder in folders:
            try:
                if not any(folder.iterdir()):
                    self.log(f"删除空文件夹: {folder.relative_to(self.target_dir)}")

                    if not self.dry_run:
                        folder.rmdir()

                    stats.deleted_folders += 1

            except OSError as exc:
                self.log(f"无法删除文件夹 {folder}: {exc}")

    def run(self) -> ExtractionStats:
        if not self.target_dir.exists():
            raise FileNotFoundError(f"文件夹不存在：{self.target_dir}")

        if not self.target_dir.is_dir():
            raise NotADirectoryError(f"不是文件夹：{self.target_dir}")

        stats = ExtractionStats()
        reserved_names: set[str] = set()

        png_files = self.find_png_files()

        if not png_files:
            self.log("没有找到需要提取的 PNG 文件。")

        for source in png_files:
            try:
                destination, renamed = self.get_available_destination(
                    source,
                    reserved_names,
                )

                reserved_names.add(destination.name.casefold())

                relative_source = source.relative_to(self.target_dir)

                if self.dry_run:
                    self.log(
                        f"[预览] {relative_source}  →  {destination.name}"
                    )
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(source), str(destination))
                    self.log(
                        f"已移动: {relative_source}  →  {destination.name}"
                    )

                stats.moved += 1

                if renamed:
                    stats.renamed += 1

            except (OSError, shutil.Error) as exc:
                stats.skipped += 1
                self.log(f"跳过文件 {source}: {exc}")

        if self.delete_empty_folders:
            self.remove_empty_folders(stats)

        return stats


class PngExtractorApp(tk.Tk):
    def __init__(self):
        super().__init__()

        self.title("PNG 图片提取工具")
        self.geometry("760x520")
        self.minsize(680, 440)

        self.message_queue: queue.Queue = queue.Queue()
        self.worker_thread: threading.Thread | None = None

        self.directory_var = tk.StringVar()
        self.delete_folders_var = tk.BooleanVar(value=True)
        self.dry_run_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(value="请选择一个目标文件夹")

        self.create_widgets()
        self.after(100, self.process_messages)

    def create_widgets(self) -> None:
        main = ttk.Frame(self, padding=12)
        main.pack(fill=tk.BOTH, expand=True)

        folder_frame = ttk.LabelFrame(main, text="目标文件夹", padding=10)
        folder_frame.pack(fill=tk.X)

        ttk.Entry(
            folder_frame,
            textvariable=self.directory_var,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8))

        self.browse_button = ttk.Button(
            folder_frame,
            text="浏览...",
            command=self.choose_directory,
        )
        self.browse_button.pack(side=tk.RIGHT)

        options_frame = ttk.LabelFrame(main, text="选项", padding=10)
        options_frame.pack(fill=tk.X, pady=(10, 0))

        ttk.Checkbutton(
            options_frame,
            text="处理完成后删除空文件夹",
            variable=self.delete_folders_var,
        ).pack(anchor=tk.W)

        ttk.Checkbutton(
            options_frame,
            text="预览模式，不实际移动或删除文件",
            variable=self.dry_run_var,
        ).pack(anchor=tk.W, pady=(6, 0))

        action_frame = ttk.Frame(main)
        action_frame.pack(fill=tk.X, pady=10)

        self.start_button = ttk.Button(
            action_frame,
            text="开始提取",
            command=self.start_extraction,
        )
        self.start_button.pack(side=tk.LEFT)

        self.clear_button = ttk.Button(
            action_frame,
            text="清空日志",
            command=self.clear_log,
        )
        self.clear_button.pack(side=tk.LEFT, padx=(8, 0))

        ttk.Label(
            action_frame,
            textvariable=self.status_var,
        ).pack(side=tk.RIGHT)

        log_frame = ttk.LabelFrame(main, text="运行日志", padding=8)
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(
            log_frame,
            wrap=tk.WORD,
            state=tk.DISABLED,
            font=("Consolas", 10),
        )
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = ttk.Scrollbar(
            log_frame,
            orient=tk.VERTICAL,
            command=self.log_text.yview,
        )
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)

        self.log_text.configure(yscrollcommand=scrollbar.set)

    def choose_directory(self) -> None:
        directory = filedialog.askdirectory(title="选择目标文件夹")

        if directory:
            self.directory_var.set(directory)
            self.status_var.set("已选择文件夹")

    def append_log(self, message: str) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def clear_log(self) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def set_running(self, running: bool) -> None:
        state = tk.DISABLED if running else tk.NORMAL

        self.start_button.configure(state=state)
        self.browse_button.configure(state=state)
        self.clear_button.configure(state=state)

    def start_extraction(self) -> None:
        directory = self.directory_var.get().strip()

        if not directory:
            messagebox.showwarning("提示", "请先选择目标文件夹。")
            return

        target = Path(directory).expanduser()

        if not target.exists() or not target.is_dir():
            messagebox.showerror("错误", "所选路径不是有效文件夹。")
            return

        self.clear_log()
        self.set_running(True)
        self.status_var.set("正在处理...")

        self.worker_thread = threading.Thread(
            target=self.run_extraction,
            args=(
                target,
                self.delete_folders_var.get(),
                self.dry_run_var.get(),
            ),
            daemon=True,
        )
        self.worker_thread.start()

    def run_extraction(
        self,
        target: Path,
        delete_empty_folders: bool,
        dry_run: bool,
    ) -> None:
        try:
            extractor = PngExtractor(
                target_dir=target,
                delete_empty_folders=delete_empty_folders,
                dry_run=dry_run,
                log_callback=lambda message: self.message_queue.put(
                    ("log", message)
                ),
            )

            stats = extractor.run()
            self.message_queue.put(("done", stats))

        except Exception as exc:
            self.message_queue.put(("error", str(exc)))

    def process_messages(self) -> None:
        try:
            while True:
                message_type, payload = self.message_queue.get_nowait()

                if message_type == "log":
                    self.append_log(payload)

                elif message_type == "done":
                    stats: ExtractionStats = payload

                    self.append_log("")
                    self.append_log(
                        "完成！"
                        f"共处理 {stats.moved} 张 PNG，"
                        f"其中 {stats.renamed} 张自动重命名，"
                        f"删除 {stats.deleted_folders} 个空文件夹，"
                        f"跳过 {stats.skipped} 个文件。"
                    )

                    self.status_var.set("处理完成")
                    self.set_running(False)

                elif message_type == "error":
                    self.append_log(f"错误：{payload}")
                    self.status_var.set("处理失败")
                    self.set_running(False)
                    messagebox.showerror("处理失败", payload)

        except queue.Empty:
            pass

        self.after(100, self.process_messages)


def main() -> None:
    app = PngExtractorApp()
    app.mainloop()


if __name__ == "__main__":
    main()
