import tkinter as tk
from tkinter import ttk, messagebox, simpledialog, filedialog
import json
import os
import sys

try:
    from ctypes import windll
    windll.shcore.SetProcessDpiAwareness(1)
except Exception:
    pass

DEFAULT_INPUT_FILE = "dcat_nl.jsonl"


class DatasetEditor:
    def __init__(self, root):
        self.root = root
        self.root.withdraw()

        self.input_filepath = self.resolve_input_file()
        if not self.input_filepath:
            sys.exit()

        self.reviewer_name = simpledialog.askstring(
            "Reviewer Login",
            "Please enter your name:\n(This will be part of your output filename)"
        )

        if not self.reviewer_name:
            sys.exit()

        safe_name = "".join(c for c in self.reviewer_name if c.isalnum() or c in ('_', '-'))
        self.output_file = f"corrected_dcat_dataset_{safe_name}.jsonl"

        self.root.deiconify()
        self.root.title(f"Dataset Editor - {self.reviewer_name}")

        self.root.minsize(1000, 700)

        try:
            self.root.state('zoomed')
        except:
            w, h = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
            self.root.geometry(f"{w - 100}x{h - 100}+50+50")

        self.data = []
        self.current_index = 0
        self.var_decision = tk.StringVar(value="")
        self.checkbox_states = {}

        self.load_data()
        self.setup_ui()
        self.show_current_entry()

        self.root.protocol("WM_DELETE_WINDOW", self.on_closing)

        self.root.bind("<Control-f>", self.show_search)
        self.entry_search.bind("<KeyRelease>", self.on_search_change)
        self.entry_search.bind("<Return>", self.find_next)
        self.entry_search.bind("<Escape>", lambda e: self.hide_search())

    def resolve_input_file(self):
        if os.path.exists(DEFAULT_INPUT_FILE):
            return DEFAULT_INPUT_FILE

        messagebox.showinfo("Select Dataset",
                            f"Could not find '{DEFAULT_INPUT_FILE}'.\nPlease select the JSONL dataset.")
        file_path = filedialog.askopenfilename(
            title="Select Input Dataset",
            filetypes=[("JSON Lines", "*.jsonl"), ("All Files", "*.*")]
        )
        return file_path if file_path else None

    def load_data(self):
        try:
            with open(self.input_filepath, 'r', encoding='utf-8') as f:
                for line in f:
                    if line.strip():
                        entry = json.loads(line)
                        entry['comment'] = ""
                        entry['flagged'] = False
                        entry['checked'] = False
                        self.data.append(entry)
        except Exception as e:
            messagebox.showerror("Error", f"Failed to read input file:\n{e}")
            sys.exit()

        if os.path.exists(self.output_file):
            try:
                with open(self.output_file, 'r', encoding='utf-8') as f:
                    user_data = [json.loads(line) for line in f if line.strip()]

                for i, u_entry in enumerate(user_data):
                    if i < len(self.data):
                        if 'nl' in u_entry: self.data[i]['nl'] = u_entry['nl']
                        if 'comment' in u_entry: self.data[i]['comment'] = u_entry['comment']
                        if 'flagged' in u_entry: self.data[i]['flagged'] = u_entry['flagged']
                        if 'checked' in u_entry: self.data[i]['checked'] = u_entry['checked']
            except Exception as e:
                messagebox.showwarning("Warning", f"Could not restore previous session:\n{e}")

        # jump to first unchecked
        for i, entry in enumerate(self.data):
            if not entry.get('checked', False):
                self.current_index = i
                break
        else:
            self.current_index = len(self.data) - 1

    def setup_ui(self):
        style = ttk.Style()
        style.theme_use('clam')
        style.configure('.', font=('Arial', 12))
        style.configure('TRadiobutton', font=('Arial', 13))

        top_frame = ttk.Frame(self.root, padding=10)
        top_frame.pack(fill=tk.X)

        self.lbl_counter = ttk.Label(top_frame, text="Entry: 0 / 0", font=("Arial", 14, "bold"))
        self.lbl_counter.pack(side=tk.LEFT)

        self.lbl_status = ttk.Label(top_frame, text=f"Reviewer: {self.reviewer_name}", foreground="blue",
                                    font=("Arial", 12))
        self.lbl_status.pack(side=tk.RIGHT, padx=10)

        paned_window = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        paned_window.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        frame_shacl = ttk.LabelFrame(paned_window, text="SHACL Code", padding=5)
        paned_window.add(frame_shacl, weight=1)

        self.txt_shacl = tk.Text(frame_shacl, wrap=tk.NONE, font=("Consolas", 12), bg="#f0f0f0")

        self.txt_shacl.tag_configure("string", foreground="#008000")
        self.txt_shacl.tag_configure("prefix", foreground="#800080", font=("Consolas", 12, "bold"))
        self.txt_shacl.tag_configure("keyword", foreground="#0000FF")
        self.txt_shacl.tag_configure("punctuation", foreground="#FF4500")

        shacl_scroll_y = ttk.Scrollbar(frame_shacl, command=self.txt_shacl.yview)
        shacl_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        shacl_scroll_x = ttk.Scrollbar(frame_shacl, orient=tk.HORIZONTAL, command=self.txt_shacl.xview)
        shacl_scroll_x.pack(side=tk.BOTTOM, fill=tk.X)
        self.txt_shacl.config(yscrollcommand=shacl_scroll_y.set, xscrollcommand=shacl_scroll_x.set)
        self.txt_shacl.pack(fill=tk.BOTH, expand=True)

        right_container = ttk.Frame(paned_window)
        paned_window.add(right_container, weight=1)

        frame_nl = ttk.LabelFrame(right_container, text="Natural Language", padding=5)
        frame_nl.pack(side=tk.TOP, fill=tk.BOTH, expand=True)

        # --- Search Bar (hidden by default) ---
        self.search_frame = ttk.Frame(frame_nl)
        self.search_frame.pack(fill=tk.X)
        self.search_frame.pack_forget()  # 默认隐藏

        ttk.Label(self.search_frame, text="Find:").pack(side=tk.LEFT)

        self.search_var = tk.StringVar()
        self.entry_search = ttk.Entry(self.search_frame, textvariable=self.search_var)
        self.entry_search.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=5)

        btn_close = ttk.Button(self.search_frame, text="✖", width=3, command=self.hide_search)
        btn_close.pack(side=tk.RIGHT)

        self.txt_nl = tk.Text(frame_nl, wrap=tk.WORD, font=("Arial", 14), undo=True, height=8)
        nl_scroll_y = ttk.Scrollbar(frame_nl, command=self.txt_nl.yview)
        nl_scroll_y.pack(side=tk.RIGHT, fill=tk.Y)
        self.txt_nl.config(yscrollcommand=nl_scroll_y.set)
        self.txt_nl.pack(fill=tk.BOTH, expand=True)

        frame_review = ttk.LabelFrame(right_container, text="Notes", padding=10)
        frame_review.pack(side=tk.BOTTOM, fill=tk.X, pady=(5, 0))

        self.radio_needs_edit = ttk.Radiobutton(
            frame_review,
            text="⚠️ Needs editing / Unsure",
            variable=self.var_decision,
            value="needs_edit"
        )

        self.radio_no_edit = ttk.Radiobutton(
            frame_review,
            text="✅ Doesn't need editing",
            variable=self.var_decision,
            value="no_edit"
        )

        self.radio_needs_edit.pack(anchor="w")
        self.radio_no_edit.pack(anchor="w")

        lbl_comment = ttk.Label(frame_review, text="Comments:", font=("Arial", 12))
        lbl_comment.pack(side=tk.TOP, anchor="w")

        self.txt_comment = tk.Text(frame_review, height=8, font=("Arial", 12))
        self.txt_comment.pack(fill=tk.X, expand=False)

        bottom_frame = ttk.Frame(self.root, padding=10)
        bottom_frame.pack(side=tk.BOTTOM, fill=tk.X)

        style.configure('W.TButton', font=('Arial', 12, 'bold'), padding=10)

        btn_prev = ttk.Button(bottom_frame, text="<< Previous (Back)", command=self.go_prev, style='W.TButton')
        btn_prev.pack(side=tk.LEFT)

        btn_next = ttk.Button(bottom_frame, text="Save & Next >>", command=self.go_next, style='W.TButton')
        btn_next.pack(side=tk.RIGHT)

    def show_search(self, event=None):
        self.search_frame.pack(fill=tk.X, before=self.txt_nl)
        self.entry_search.focus_set()
        self.entry_search.select_range(0, tk.END)


    def hide_search(self):
        self.search_frame.pack_forget()
        self.txt_nl.tag_remove("highlight", "1.0", tk.END)


    def on_search_change(self, event=None):
        term = self.search_var.get()
        self.highlight_all(term)


    def highlight_all(self, term):
        text = self.txt_nl
        text.tag_remove("highlight", "1.0", tk.END)

        if not term:
            return

        start = "1.0"
        while True:
            pos = text.search(term, start, stopindex=tk.END, nocase=True)
            if not pos:
                break

            end = f"{pos}+{len(term)}c"
            text.tag_add("highlight", pos, end)
            start = end

        text.tag_config("highlight", background="yellow")

    def open_search_dialog(self, event=None):
        search_term = simpledialog.askstring("Search", "Enter text to find:")

        if not search_term:
            return

        self.highlight_text(search_term)
    
    def highlight_text(self, search_term):
        text_widget = self.txt_nl

        # 清除旧高亮
        text_widget.tag_remove("highlight", "1.0", tk.END)

        if not search_term:
            return

        start = "1.0"

        while True:
            pos = text_widget.search(search_term, start, stopindex=tk.END)

            if not pos:
                break

            end = f"{pos}+{len(search_term)}c"

            text_widget.tag_add("highlight", pos, end)

            start = end

        # 设置高亮样式
        text_widget.tag_config("highlight", background="yellow")
        text_widget.see("highlight.first")

    def find_next(self, event=None):
        term = self.search_var.get()
        if not term:
            return

        text = self.txt_nl
        current = text.index(tk.INSERT)

        pos = text.search(term, current, stopindex=tk.END, nocase=True)

        if not pos:
            pos = text.search(term, "1.0", stopindex=tk.END, nocase=True)

        if pos:
            end = f"{pos}+{len(term)}c"
            text.tag_remove("current_match", "1.0", tk.END)
            text.tag_add("current_match", pos, end)
            text.tag_config("current_match", background="orange")

            text.mark_set(tk.INSERT, end)
            text.see(pos)

    def show_current_entry(self):
        if not self.data: return
        entry = self.data[self.current_index]

        flag_status = " [FLAGGED]" if entry.get('flagged') else ""
        self.lbl_counter.config(
            text=f"Entry: {self.current_index + 1} / {len(self.data)}   (ID: {entry.get('id', 'Unknown')}){flag_status}")

        self.txt_shacl.config(state=tk.NORMAL)
        self.txt_shacl.delete("1.0", tk.END)
        self.txt_shacl.insert("1.0", entry.get('shacl', ''))

        self.apply_shacl_highlighting()
        self.inject_property_checkboxes()

        self.txt_shacl.config(state=tk.DISABLED)

        self.txt_nl.delete("1.0", tk.END)
        self.txt_nl.insert("1.0", entry.get('nl', ''))

        self.txt_comment.delete("1.0", tk.END)
        self.txt_comment.insert("1.0", entry.get('comment', ''))

        if entry.get('checked'):
            if entry.get('flagged'):
                self.var_decision.set("needs_edit")
            else:
                self.var_decision.set("no_edit")
        else:
            self.var_decision.set("")

    def update_internal_data(self):
        if not self.data: return

        nl_text = self.txt_nl.get("1.0", "end-1c")
        nl_text = " ".join(nl_text.splitlines())
        self.data[self.current_index]['nl'] = nl_text

        self.data[self.current_index]['comment'] = self.txt_comment.get("1.0", "end-1c")

        decision = self.var_decision.get()

        if decision == "needs_edit":
            self.data[self.current_index]['flagged'] = True
            self.data[self.current_index]['checked'] = True

        elif decision == "no_edit":
            self.data[self.current_index]['flagged'] = False
            self.data[self.current_index]['checked'] = True

    def save_to_disk_quietly(self):
        try:
            with open(self.output_file, 'w', encoding='utf-8') as f:
                for entry in self.data:
                    f.write(json.dumps(entry) + "\n")
            self.lbl_status.config(text=f"Saved to {self.output_file}", foreground="green")
        except Exception as e:
            self.lbl_status.config(text=f"Error saving! {e}", foreground="red")

    def go_next(self):
        if self.var_decision.get() == "":
            messagebox.showwarning("Warning", "Please choose one option before proceeding.")
            return

        self.update_internal_data()
        self.save_to_disk_quietly()
        if self.current_index < len(self.data) - 1:
            self.current_index += 1
            self.show_current_entry()
        else:
            messagebox.showinfo("End", "You have reached the last entry.")

    def go_prev(self):
        self.update_internal_data()
        self.save_to_disk_quietly()
        if self.current_index > 0:
            self.current_index -= 1
            self.show_current_entry()

    def on_closing(self):
        self.update_internal_data()
        self.save_to_disk_quietly()
        self.root.destroy()

    def apply_shacl_highlighting(self):
        patterns = {
            "string": r'"[^"]*"',
            "prefix": r'@prefix\s+\w+:',
            "keyword": r'\b(sh|rdf|rdfs|owl|xsd|chemrof):[a-zA-Z0-9_]+\b',
            "punctuation": r'[\[\]\(\)\.,;]'
        }

        for tag, pattern in patterns.items():
            idx = "1.0"
            count = tk.IntVar()
            while True:
                idx = self.txt_shacl.search(pattern, idx, stopindex=tk.END, regexp=True, count=count)
                if not idx:
                    break
                end_idx = f"{idx}+{count.get()}c"
                self.txt_shacl.tag_add(tag, idx, end_idx)
                idx = end_idx

    def inject_property_checkboxes(self):
        idx = "1.0"
        count = tk.IntVar()
        match_index = 0

        if self.current_index not in self.checkbox_states:
            self.checkbox_states[self.current_index] = []
            is_new_entry = True
        else:
            is_new_entry = False

        while True:
            idx = self.txt_shacl.search(r'\[\s*sh:', idx, stopindex=tk.END, regexp=True, count=count)
            if not idx:
                break

            if is_new_entry:
                var = tk.BooleanVar(value=False)
                self.checkbox_states[self.current_index].append(var)
            else:
                var = self.checkbox_states[self.current_index][match_index]

            def create_toggle_handler(v, l):
                def toggle(event):
                    v.set(not v.get())
                    l.config(
                        text="\u2611" if v.get() else "\u2610",
                        fg="#008000" if v.get() else "#000000"
                    )
                return toggle

            initial_text = "\u2611" if var.get() else "\u2610"
            initial_fg = "#008000" if var.get() else "#000000"

            cb_label = tk.Label(
                self.txt_shacl,
                text=initial_text,
                font=("Consolas", 25, "bold"),
                bg="#f0f0f0",
                fg=initial_fg,
                cursor="hand2",
                padx=2, pady=0
            )

            cb_label.bind("<Button-1>", create_toggle_handler(var, cb_label))

            self.txt_shacl.window_create(idx, window=cb_label)

            match_length = count.get()
            idx = f"{idx}+{match_length + 1}c"

            match_index += 1

    

if __name__ == "__main__":
    root = tk.Tk()
    app = DatasetEditor(root)
    root.mainloop()
