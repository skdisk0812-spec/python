import random
import time
from dataclasses import dataclass
from typing import List, Tuple, Optional

try:
    import tkinter as tk
except Exception:
    print("Tkinter is required. On Linux: sudo apt install python3-tk")
    raise

# --- Game constants ---
CELL = 30
COLS = 10
ROWS = 20
SIDE_W = 180
PADDING = 12

SHAPES = {
    "I": [
        [(0, 1), (1, 1), (2, 1), (3, 1)],
        [(2, 0), (2, 1), (2, 2), (2, 3)],
        [(0, 2), (1, 2), (2, 2), (3, 2)],
        [(1, 0), (1, 1), (1, 2), (1, 3)],
    ],
    "O": [[(1, 0), (2, 0), (1, 1), (2, 1)]] * 4,
    "T": [
        [(1, 0), (0, 1), (1, 1), (2, 1)],
        [(1, 0), (1, 1), (2, 1), (1, 2)],
        [(0, 1), (1, 1), (2, 1), (1, 2)],
        [(1, 0), (0, 1), (1, 1), (1, 2)],
    ],
    "S": [
        [(1, 0), (2, 0), (0, 1), (1, 1)],
        [(1, 0), (1, 1), (2, 1), (2, 2)],
        [(1, 1), (2, 1), (0, 2), (1, 2)],
        [(0, 0), (0, 1), (1, 1), (1, 2)],
    ],
    "Z": [
        [(0, 0), (1, 0), (1, 1), (2, 1)],
        [(2, 0), (1, 1), (2, 1), (1, 2)],
        [(0, 1), (1, 1), (1, 2), (2, 2)],
        [(1, 0), (0, 1), (1, 1), (0, 2)],
    ],
    "J": [
        [(0, 0), (0, 1), (1, 1), (2, 1)],
        [(1, 0), (2, 0), (1, 1), (1, 2)],
        [(0, 1), (1, 1), (2, 1), (2, 2)],
        [(1, 0), (1, 1), (0, 2), (1, 2)],
    ],
    "L": [
        [(2, 0), (0, 1), (1, 1), (2, 1)],
        [(1, 0), (1, 1), (1, 2), (2, 2)],
        [(0, 1), (1, 1), (2, 1), (0, 2)],
        [(0, 0), (1, 0), (1, 1), (1, 2)],
    ],
}

COLORS = {
    "I": "#00BCD4",
    "O": "#FFC107",
    "T": "#9C27B0",
    "S": "#4CAF50",
    "Z": "#F44336",
    "J": "#3F51B5",
    "L": "#FF9800",
}


@dataclass
class Piece:
    kind: str
    x: int
    y: int
    r: int = 0

    @property
    def cells(self) -> List[Tuple[int, int]]:
        return SHAPES[self.kind][self.r]

    def rotated(self, dr: int) -> "Piece":
        return Piece(self.kind, self.x, self.y, (self.r + dr) % 4)


class TetrisGame:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("Tetris (Tkinter)")

        self.canvas = tk.Canvas(self.root, width=COLS * CELL, height=ROWS * CELL,
                                bg="#121212", highlightthickness=0)
        self.canvas.grid(row=0, column=0, padx=(PADDING, 0), pady=PADDING)

        self.side = tk.Canvas(self.root, width=SIDE_W, height=ROWS * CELL,
                              bg="#1e1e1e", highlightthickness=0)
        self.side.grid(row=0, column=1, padx=(PADDING, PADDING),
                       pady=PADDING, sticky="ns")

        self.next_canvas = tk.Canvas(self.side, width=SIDE_W - 2 * PADDING,
                                     height=120, bg="#1e1e1e", highlightthickness=0)
        self.next_canvas.place(x=PADDING, y=PADDING)

        self.info_text = self.side.create_text(SIDE_W // 2, 160, text="", fill="#e0e0e0",
                                               font=("Segoe UI", 12), anchor="n")
        self.message_id = self.side.create_text(SIDE_W // 2, ROWS * CELL - 120, text="",
                                                fill="#fafafa", font=("Segoe UI", 14, "bold"), anchor="n")

        self.root.bind("<KeyPress>", self.on_key)
        self.reset()
        self.game_loop()

    # --- Core game ---
    def reset(self):
        self.board = [[None for _ in range(COLS)] for _ in range(ROWS)]
        self.score = 0
        self.level = 0
        self.lines_cleared = 0
        self.paused = False
        self.game_over = False
        self.last_tick = time.time()
        self.bag = []
        self.next_kind = self.take_from_bag()
        self.spawn_piece()
        self.render()

    def take_from_bag(self):
        if not self.bag:
            self.bag = list(SHAPES.keys())
            random.shuffle(self.bag)
        return self.bag.pop()

    def spawn_piece(self):
        kind = self.next_kind
        self.next_kind = self.take_from_bag()
        self.current = Piece(kind, x=3, y=-2, r=0)
        if self.collides(self.current):
            self.game_over = True
            self.set_message("GAME OVER\nPress R to restart")

    def collides(self, piece):
        for cx, cy in piece.cells:
            x, y = piece.x + cx, piece.y + cy
            if x < 0 or x >= COLS or y >= ROWS:
                return True
            if y < 0:
                continue
            if self.board[y][x]:
                return True
        return False

    def try_move(self, dx, dy):
        moved = Piece(self.current.kind, self.current.x + dx, self.current.y + dy, self.current.r)
        if not self.collides(moved):
            self.current = moved
            return True
        return False

    def try_rotate(self):
        """SRS 회전 (모든 블록 360° 회전 허용, 시계방향)"""
        rotated = self.current.rotated(1)
        # 표준화된 wall kick 오프셋 (단순화 버전)
        kicks = [
            (0, 0), (1, 0), (-1, 0), (0, -1),
            (2, 0), (-2, 0), (0, 1), (1, -1), (-1, -1)
        ]
        for ox, oy in kicks:
            test = Piece(rotated.kind, rotated.x + ox, rotated.y + oy, rotated.r)
            if not self.collides(test):
                self.current = test
                return True
        return False

    def hard_drop(self):
        while self.try_move(0, 1):
            self.score += 2
        self.lock_piece()

    def lock_piece(self):
        for cx, cy in self.current.cells:
            x, y = self.current.x + cx, self.current.y + cy
            if y < 0:
                self.game_over = True
                self.set_message("GAME OVER\nPress R to restart")
                return
            self.board[y][x] = self.current.kind
        cleared = self.clear_lines()
        if cleared:
            base = {1: 100, 2: 300, 3: 500, 4: 800}[cleared]
            self.score += base
            self.level = self.score // 1000
        self.spawn_piece()

    def clear_lines(self):
        full = [i for i, r in enumerate(self.board) if all(r)]
        for i in reversed(full):
            del self.board[i]
            self.board.insert(0, [None for _ in range(COLS)])
        return len(full)

    # --- Rendering ---
    def draw_cell(self, c, x, y, color):
        x0, y0, x1, y1 = x * CELL, y * CELL, (x + 1) * CELL, (y + 1) * CELL
        c.create_rectangle(x0, y0, x1, y1, fill=color, outline="#101010")
        c.create_rectangle(x0, y0, x1, y0 + 5, fill="#ffffff", stipple="gray25", outline="")

    def render(self):
        self.canvas.delete("all")
        for y in range(ROWS):
            for x in range(COLS):
                if self.board[y][x]:
                    self.draw_cell(self.canvas, x, y, COLORS[self.board[y][x]])
        if self.current:
            for cx, cy in self.current.cells:
                x, y = self.current.x + cx, self.current.y + cy
                if y >= 0:
                    self.draw_cell(self.canvas, x, y, COLORS[self.current.kind])
        for x in range(COLS + 1):
            self.canvas.create_line(x * CELL, 0, x * CELL, ROWS * CELL, fill="#222")
        for y in range(ROWS + 1):
            self.canvas.create_line(0, y * CELL, COLS * CELL, y * CELL, fill="#222")

        # Info panel
        info = (
            f"Score: {self.score}\n"
            f"Level: {self.level}\n\n"
            "←/→: Move\n"
            "↓: Soft Drop\n"
            "↑: Rotate (Clockwise)\n"
            "Space: Hard Drop\n"
            "P: Pause   R: Restart"
        )
        self.side.itemconfig(self.info_text, text=info)
        self.set_message("PAUSED" if self.paused else "")

    def set_message(self, text):
        self.side.itemconfig(self.message_id, text=text)

    # --- Input ---
    def on_key(self, e):
        k = e.keysym
        if k in ("p", "P"):
            self.paused = not self.paused
        elif k in ("r", "R"):
            self.reset()
        elif not self.paused and not self.game_over:
            if k == "Left":
                self.try_move(-1, 0)
            elif k == "Right":
                self.try_move(1, 0)
            elif k == "Down":
                self.try_move(0, 1)
                self.score += 1
            elif k == "Up":
                self.try_rotate()
            elif k.lower() == "space":
                self.hard_drop()
        self.render()

    def game_loop(self):
        if not self.paused and not self.game_over:
            now = time.time()
            if (now - self.last_tick) * 1000 >= max(80, 800 - self.level * 55):
                if not self.try_move(0, 1):
                    self.lock_piece()
                self.last_tick = now
        self.render()
        self.root.after(16, self.game_loop)

    def run(self):
        self.root.resizable(False, False)
        self.root.mainloop()


if __name__ == "__main__":
    TetrisGame().run()
