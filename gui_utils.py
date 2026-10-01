import asyncio
import ctypes
import inspect
import logging
import queue
import threading
import tkinter as tk
import sys

from tkinter import ttk
from typing import Optional


_logger = logging.getLogger(__name__)


class IndicatorLight(tk.Canvas):
    """Circular light with steady gray, yellow, green, and red colors."""

    COLOR_MAP = {
        "gray": {
            "fill": "#BDBDBD",
            "outline": "#9E9D9D",
            "glow": "#E0E0E0",
        },
        "yellow": {
            "fill": "#FFCA28",
            "outline": "#FFA000",
            "glow": "#FFF59D",
        },
        "green": {
            "fill": "#4CAF50",
            "outline": "#2E7D32",
            "glow": "#A5D6A7",
        },
        "red": {
            "fill": "#F44336",
            "outline": "#C62828",
            "glow": "#FFCDD2",
        },
    }

    _size: int
    _color: str

    def __init__(self, parent, size: int = 18, **kwargs):
        super().__init__(parent, width=size, height=size, highlightthickness=0, **kwargs)
        self._size = size
        self._color = "gray"
        self._draw()

    def _normalize_color(self, color: str) -> str:
        if color not in self.COLOR_MAP:
            _logger.warning("Unknown indicator color '%s', falling back to gray", color)
            color = "gray"
        return color

    def set_color(self, color: str) -> None:
        """Set a steady gray, yellow, green, or red light."""
        self._color = self._normalize_color(color)
        self._draw()

    def get_color(self) -> str:
        """Return the selected color."""
        return self._color

    def _draw(self, color: Optional[str] = None) -> None:
        self.delete("all")
        palette = self.COLOR_MAP[self._color if color is None else color]
        s = self._size
        pad = 2
        # Base circle
        self.create_oval(
            pad,
            pad,
            s - pad,
            s - pad,
            fill=palette["fill"],
            outline=palette["outline"],
            width=1.5,
        )
        # Glossy highlight
        self.create_oval(
            pad + 2,
            pad + 1,
            pad + max(3, (s - 2 * pad) // 2),
            pad + max(3, (s - 2 * pad) // 3),
            fill=palette["glow"],
            outline="",
        )


class BlinkingIndicatorLight(IndicatorLight):
    """Indicator light that can alternate its selected color with an off color."""

    _blink_timer: Optional[str]
    _blink_interval_sec: float
    _blink_off_color: str
    _blink_on: bool

    def __init__(self, parent, size: int = 18, **kwargs):
        super().__init__(parent, size=size, **kwargs)
        self._blink_timer: Optional[str] = None
        self._blink_interval_sec = 0.5
        self._blink_off_color = "gray"
        self._blink_on = False

    def set_color(self, color: str, blinking: bool = False, blinking_interval_sec: float = 0.5) -> None:
        """Set the selected color and optionally blink at the given interval in seconds."""
        self._cancel_blink()
        self._color = self._normalize_color(color)
        if blinking:
            self.start_blink(interval_sec=blinking_interval_sec)
        else:
            self._draw()

    def start_blink(self, interval_sec: float = 0.5, off_color: str = "gray") -> None:
        """Alternate color and off_color every interval_sec, starting with color.

        Colors use COLOR_MAP. Call this and other widget methods on the Tk thread.
        Calling again replaces the active blink; stop_blink keeps color steady.
        """
        self._cancel_blink()
        self._blink_off_color = self._normalize_color(off_color)
        self._blink_interval_sec = interval_sec
        self._blink_on = True
        self._draw()
        self._blink_timer = self.after(int(interval_sec * 1000), self._toggle_blink)

    def stop_blink(self) -> None:
        """Stop blinking and display the selected color steadily."""
        self._cancel_blink()
        self._draw()

    def _cancel_blink(self) -> None:
        if self._blink_timer is not None:
            self.after_cancel(self._blink_timer)
            self._blink_timer = None

    def _toggle_blink(self) -> None:
        self._blink_timer = None
        self._blink_on = not self._blink_on
        self._draw(self._color if self._blink_on else self._blink_off_color)
        self._blink_timer = self.after(int(self._blink_interval_sec * 1000), self._toggle_blink)

    def destroy(self) -> None:
        self._cancel_blink()
        super().destroy()


class GuiApp:
    """Base class for GUI applications."""
    _root: tk.Tk
    _ui_queue: queue.Queue
    _is_closing: bool
    _poll_timer: Optional[str]

    def __init__(self,
            title: str,
            width: Optional[int] = None,
            height: Optional[int] = None,
            resizeable: bool = True,
        ):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except:
            pass    

        self._root = tk.Tk()
        self._root.title(title)
        if width is not None and height is not None:
            self._center_window(width, height)
            self._root.minsize(width, height)
            self._root.resizable(resizeable, resizeable)

        self._ui_queue: queue.Queue = queue.Queue()
        self._is_closing = False
        self._poll_timer = self._root.after(50, self._poll_ui_queue)
        self._root.protocol("WM_DELETE_WINDOW", self.on_close)

        # Apply native styling
        self._style = ttk.Style(self._root)
        try:
            if sys.platform == "darwin":
                self._style.theme_use("aqua")
            elif sys.platform == "win32":
                self._style.theme_use("arc")
        except Exception:
            pass

        self._async_loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run_background_loop, daemon=True)
        self._thread.start()  

    @property
    def root(self) -> tk.Tk:
        return self._root

    @property
    def style(self) -> ttk.Style:
        return self._style

    def run(self) -> None:
        """Start the Tkinter event loop."""
        self._root.mainloop()

    def on_close(self) -> None:
        """Clean shutdown when closing window."""
        self._is_closing = True

        pending_tasks = asyncio.all_tasks(self._async_loop)
        for task in pending_tasks:
            task.cancel()
        self._async_loop.stop()

        if self._poll_timer:
            try:
                self._root.after_cancel(self._poll_timer)
            except Exception:
                pass
            self._poll_timer = None
        try:
            self._root.quit()
        except Exception:
            pass
        self._root.destroy()

    def post_ui_task(self, callback) -> None:
        """Enqueues a task to be executed on the Tkinter main thread."""
        if self._is_closing:
            return
        self._ui_queue.put(callback)

    def post_background_task(self, callback) -> None:
        """Schedules an asynchronous task to run in the background event loop."""
        if self._is_closing:
            return
        if inspect.iscoroutinefunction(callback):
            asyncio.run_coroutine_threadsafe(callback(), self._async_loop)
        else:
            self._async_loop.call_soon_threadsafe(callback)

    def _drain_ui_queue(self) -> None:
        """Processes all pending callbacks in the queue immediately without scheduling next poll."""
        try:
            while True:
                callback = self._ui_queue.get_nowait()
                try:
                    callback()
                except Exception as ex:
                    _logger.debug("Error running UI queue callback: %s", ex)
        except queue.Empty:
            pass

    def _poll_ui_queue(self) -> None:
        """Processes pending UI callbacks on the main thread."""
        if self._is_closing:
            return
        self._drain_ui_queue()
        if not self._is_closing:
            self._poll_timer = self._root.after(50, self._poll_ui_queue)

    def _center_window(self, width, height):
        # Force Tkinter to evaluate and layout widgets
        self._root.update_idletasks()
        
        # Retrieve screen width and height
        screen_width = self._root.winfo_screenwidth()
        screen_height = self._root.winfo_screenheight()
        
        # Calculate X and Y coordinates for centering
        x = (screen_width - width) // 2
        y = (screen_height - height) // 2
        
        # Apply the geometry string with size and position
        self._root.geometry(f"{width}x{height}+{x}+{y}")

    def _run_background_loop(self) -> None:
        asyncio.set_event_loop(self._async_loop)
        self._async_loop.run_forever()
