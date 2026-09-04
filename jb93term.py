# jb93term.py
# 
# Reusable console functions for any text-based script
# 
# NO external dependencies required!
# (enhanced keyboard support available with pynput module)
# 
# TODO:
# - separate menu() and multiselect menu logic; too many issues to handle with combining them
# 	- OR, improve the logic to handle both cases in different ways

import math, os, platform, shutil, sys, tempfile, textwrap, time, webbrowser
from datetime import date
from itertools import chain

# THIS MONITORS KEYBOARD INPUT SYSTEM-WIDE, EVEN WHEN TYPING IN OTHER PROCESSES
# not what we want!!
# import importlib.util
# pynput_spec = importlib.util.find_spec("pynput")
# PYNPUT_DETECTED = (pynput_spec is not None)
# if PYNPUT_DETECTED:
# 	from pynput import keyboard

# https://stackoverflow.com/a/70664652
# cross-platform key detection
import contextlib as _contextlib

try:
	import msvcrt as _msvcrt

	# Length 0 sequences, length 1 sequences...
	_ESCAPE_SEQUENCES = [frozenset(("\x00", "\xe0"))]

	_next_input = _msvcrt.getwch

	_set_terminal_raw = _contextlib.nullcontext

	_input_ready = _msvcrt.kbhit

except ImportError:  # Unix
	import sys as _sys, tty as _tty, termios as _termios, \
		select as _select, functools as _functools

	# Length 0 sequences, length 1 sequences...
	_ESCAPE_SEQUENCES = [
		frozenset(("\x1b",)),
		frozenset(("\x1b\x5b", "\x1b\x4f"))]

	@_contextlib.contextmanager
	def _set_terminal_raw():
		fd = _sys.stdin.fileno()
		old_settings = _termios.tcgetattr(fd)
		try:
			_tty.setraw(_sys.stdin.fileno())
			yield
		finally:
			_termios.tcsetattr(fd, _termios.TCSADRAIN, old_settings)

	_next_input = _functools.partial(_sys.stdin.read, 1)

	def _input_ready():
		return _select.select([_sys.stdin], [], [], 0) == ([_sys.stdin], [], [])

_MAX_ESCAPE_SEQUENCE_LENGTH = len(_ESCAPE_SEQUENCES)

# ==============================================================
# Main class
# 
# Contains convenience functions for terminal scripts
# 
class Terminal:
	COLOR_SUPPORT = True

	fgcolors = {
		"reset": 		"\033[0m",
		"black": 		"\033[30m",
		"red": 			"\033[31m",	
		"green": 		"\033[32m",
		"yellow": 		"\033[33m",
		"blue": 		"\033[34m",
		"purple": 		"\033[35m",
		"lightblue": 	"\033[36m",
		"gray": 		"\033[37m",
		"default": 		"\033[39m",
		"darkgray": 	"\033[1;30m",
		"lightred": 	"\033[1;31m",
		"lime": 		"\033[1;32m",
		"lightyellow": 	"\033[1;33m",
		"brightblue": 	"\033[1;34m",
		"magenta": 		"\033[1;35m",
		"cyan": 		"\033[1;36m",
		"white": 		"\033[1;37m"
	}

	bgcolors = {
		"reset": 		"\033[0m",
		"black": 		"\033[40m",
		"red": 			"\033[41m",	
		"green": 		"\033[42m",
		"yellow": 		"\033[43m",
		"blue": 		"\033[44m",
		"purple": 		"\033[45m",
		"lightblue": 	"\033[46m",
		"gray": 		"\033[47m",
		"default": 		"\033[49m",
		"darkgray": 	"\033[1;40m",
		"lightred": 	"\033[1;41m",
		"lime": 		"\033[1;42m",
		"lightyellow": 	"\033[1;43m",
		"brightblue": 	"\033[1;44m",
		"magenta": 		"\033[1;45m",
		"cyan": 		"\033[1;46m",
		"white": 		"\033[1;47m"
	}

	mode = {
		"reset":		"\033[0m",
		"bold":			"\033[1m",
		"dim":			"\033[2m",
		"italic":		"\033[3m",
		"underline":	"\033[4m",
		"blinking":		"\033[5m",
		"inverse":		"\033[7m",
		"hidden":		"\033[8m",
		"strike":		"\033[9m",
	}
	modereset = {
		"bold":			"\033[22m",
		"dim":			"\033[22m",
		"italic":		"\033[23m",
		"underline":	"\033[24m",
		"blinking":		"\033[25m",
		"inverse":		"\033[27m",
		"hidden":		"\033[28m",
		"strike":		"\033[29m",
	}

	boxchars = {
		"normal": {
			"top": 		"\u250c\u2500\u2510", 	# ┌─┐
			"middle": 	"\u2502 \u2502",		# │ │
			"bottom": 	"\u2514\u2500\u2518",	# └─┘
			"vsplit":	"\u251c\u2500\u2524",	# ├─┤
			"vsplit_2":	"\u255e\u2550\u2561",	# ╞═╡
			"hsplit":	"\u252c\u2502\u2534",	# ┬│┴
			"cross":	"\u253c",				# ┼
			"shadow":	"\u2591\u2592\u2593\u2588"
		},
		"double": {
			"top":		"\u2554\u2550\u2557",	# ╔═╗
			"middle":	"\u2551 \u2551",		# ║ ║
			"bottom":	"\u255a\u2550\u255d",	# ╚═╝
			"vsplit":	"\u2560\u2550\u2563",	# ╠═╣
			"vsplit_2":	"\u255f\u2500\u2562",	# ╟─╢
			"hsplit":	"\u2566\u2551\u2569",
			"cross":	"\u256c",
			"shadow":	"\u2591\u2592\u2593\u2588"
		}
	}

	# shorthand for the reset escape code
	@classmethod
	def _reset(cls):
		return cls.fgcolors["reset"]

	@classmethod
	def _color(cls, *, bg=None, fg=None):
		r = ""
		if bg:
			r += cls.bgcolors[bg]
		if fg:
			r += cls.fgcolors[fg]

		if r == "":
			return cls._reset()

		return r

	# ==========================================================
	# text printing functions
	# 
	
	@classmethod
	def print(cls, strn, *, color=None, bgcolor=None, word_wrap=True, word_wrap_col=0, **kwargs):
		textlines = [strn]

		if word_wrap:
			if word_wrap_col <= 0:
				word_wrap_col = shutil.get_terminal_size((80, 20))[0]
			textlines = textwrap.wrap(strn, word_wrap_col)

		for line in textlines:
			if bgcolor:
				print(cls.bgcolors[bgcolor], end="")

			if color:
				print(cls.fgcolors[color] + line + cls._reset(), **kwargs)
			else:
				print(line, **kwargs)

			if bgcolor:
				print(cls._reset(), end="")

	@classmethod
	def error(cls, strn, *, color=None, **kwargs):
		cls.print(strn, color=color, file=sys.stderr, **kwargs)

	@classmethod
	def print_error(cls, strn, *, to_stdout=True, to_stderr=False, **kwargs):
		if to_stdout:
			cls.print(strn, color="red", **kwargs)
		if to_stderr:
			cls.error(strn, color="red", **kwargs)

	@classmethod
	def print_warning(cls, strn, *, to_stdout=True, to_stderr=False, **kwargs):
		if to_stdout:
			cls.print(strn, color="yellow", **kwargs)
		if to_stderr:
			cls.error(strn, color="yellow", **kwargs)

	@classmethod
	def print_success(cls, strn, **kwargs):
		cls.print(strn, color="green", **kwargs)

	@classmethod
	def print_msg(cls, strn, **kwargs):
		cls.print(strn, color="lightblue", **kwargs)

	@classmethod
	def print_dim(cls, strn, **kwargs):
		cls.print(strn, color="darkgray", **kwargs)

	@classmethod
	def print_labelled(cls, label, strn, *, color="lightblue", seperator=":", **kwargs):
		if type(strn) is not str:
			strn = str(strn)
			
		print(cls.fgcolors[color] + label + cls._reset() + seperator + " " + strn, **kwargs)

	@classmethod
	def print_underline(cls, strn, **kwargs):
		cls.print(cls.mode["underline"] + strn + cls.modereset["underline"], **kwargs)

	# prints black text on light gray background (by default)
	@classmethod
	def print_highlighted(cls, strn, *, color="gray", textcolor="black", end="\n"):
		cls.print(strn, bgcolor=color, color=textcolor, end=end)

	# ==========================================================
	# terminal utility functions
	# 
	
	# clears the terminal window
	@classmethod
	def clear(cls, *, bgcolor=None):
		if bgcolor:
			print("\033[H" + cls.bgcolors[bgcolor] + "\033[J", end="")
		else:
			print("\033[H\033[J", end="")

	# bell audio cue
	@classmethod
	def bell(cls):
		print("\a", end="")

	# Press any key to continue...
	@classmethod
	def pause(cls, *, prompt="Press any key to continue..."):
		if not prompt or prompt.strip() == "":
			prompt = "Press any key to continue..."

		if platform.system() == "Windows":
			print(prompt)
			os.system("pause > nul")
		else:
			os.system(f"/bin/bash -c 'read -s -n 1 -p \"{prompt}\"'")
			print()

	# clears screen and draws a title bar on the top line
	@classmethod
	def screen(cls, strn, *, bgcolor=None, barcolor="gray", textcolor="black"):
		cls.clear(bgcolor=bgcolor)

		screensize = shutil.get_terminal_size((80, 20))
		print(cls._color(bg=barcolor, fg=textcolor) + strn + (" " * (screensize[0]-len(strn))) + "\033[E" + cls._reset() + "\n")

	@classmethod
	def cursor_pos(cls, x, y, *, returncode=False):
		if returncode:
			return f"\033[{y};{x}H"
		print(f"\033[{y};{x}H", end="")
		# print(f"\033[{x}C", end="")
		# print(f"\033[{x}G")

	@classmethod
	def cursor_col(cls, c, *, returncode=False):
		if returncode:
			return f"\033[{c}G"
		print(f"\033[{c}G", end="")

	@classmethod
	def cursor_home(cls, *, returncode=False):
		if returncode:
			return f"\033[H"
		print("\033[H", end="")

	@classmethod
	def cursor_savepos(cls, *, returncode=False):
		if returncode:
			return f"\033[s"
		print("\033[s", end="")

	@classmethod
	def cursor_restorepos(cls, *, returncode=False):
		if returncode:
			return f"\033[u"
		print("\033[u", end="")

	# returns current working directory
	@classmethod
	def getcwd(cls):
		return os.getcwd() # os.path.realpath(__file__)

	@classmethod
	def open_url(cls, url):
		webbrowser.open_new_tab(url)

	# ==========================================================
	# input helper functions
	# 
	
	# helper for input prompts where a default value is supplied if nothing is entered
	@classmethod
	def input(cls, prompt, defaultvalue="", *, allow_ctrlc=False, cancelvalue=None, cancelmessage="** cancelled"):
		try:
			resp = ""
			if defaultvalue.strip() == "":
				resp = input(prompt).strip()
			else:
				resp = input("%s [%s] " % (prompt, defaultvalue)).strip()
			
			if resp == "":
				return defaultvalue
			else:
				return resp
		except KeyboardInterrupt as kint:
			if not allow_ctrlc:
				raise kint

			print()
			cls.print_error(cancelmessage)

			return cancelvalue

	# helper for yes/no prompts
	# returns True for yes, False for no
	@classmethod
	def input_yn(cls, prompt, defaultvalue=True, *, warning=False, allow_ctrlc=False, cancelvalue=False, cancelmessage="** cancelled"):
		try:
			p = "[Y/n]"
			if not defaultvalue:
				p = "[y/N]"

			if warning:
				prompt = cls.fgcolors["yellow"] + prompt + cls._reset()

			resp = input("%s %s " % (prompt, p)).lower().strip()
			if not resp:
				return defaultvalue

			return resp[0] == "y"
		except KeyboardInterrupt as kint:
			if not allow_ctrlc:
				raise kint

			print()
			cls.print_error(cancelmessage)

			return cancelvalue

	# helper for numeric input prompts
	@classmethod
	def input_int(cls, prompt, defaultvalue=None, *, retry_until_valid=True, within=None, allow_ctrlc=False, cancelvalue=None, cancelmessage="** cancelled"):
		try:
			respint = None
			if defaultvalue is not None:
				prompt = prompt + " [" + str(defaultvalue) + "] "

			while respint is None and retry_until_valid:
				try:
					resp = input(prompt).lower().strip()

					if resp == "" and defaultvalue is not None:
						return int(defaultvalue)

					respint = int(resp)

					if within is not None:
						if respint < within[0] or respint > within[1]:
							respint = None
							cls.print_warning("* Number outside of allowed range")
				except ValueError as e:
					cls.print_warning("* Not a valid number")

			if respint is None and defaultvalue is not None:
				return int(defaultvalue)

			return respint
		except KeyboardInterrupt as kint:
			if not allow_ctrlc:
				raise kint

			print()
			cls.print_error(cancelmessage, to_stderr=False)

			return cancelvalue

	# helper function for simple text-based "type number of your selection and press enter" menus
	# 
	@classmethod
	def numbermenu(cls, prompt, choices, defaultindex=None, *, title="Select an option", format_str="%s", format_fields=None, allow_ctrlc=True, instructions=None, return_index=False, clear_on_finish=True, on_print_option=None, hidden_options={}, indent_options=2):
		choices = [c for c in choices if c is not None]
		running = True

		prompt_lines = prompt.split("\n")
		if instructions is None:
			# instructions = [
			# 	"Type the number of your selection,",
			# 	"and press ENTER.",
			# 	"===================================="
			# ]
			instructions = []

		for i in instructions:
			prompt_lines.append(i)
		prompt_lines.append("")

		def format_option(i, prefix=""):
			opt = choices[i]
			if type(opt) is str:
				return prefix + opt
			#elif type(opt) is dict:
			else:
				if format_fields is None or format_fields == []:
					return prefix + str(opt)

				values = []

				for f in format_fields:
					values.append(opt[f])

				return prefix + (format_str % tuple(values))

		while running:
			cls.screen(title)
			for p in prompt_lines:
				print(p)

			cls.print_msg("Choose a menu option:")

			for i in range(len(choices)):
				printed = False
				if on_print_option:
					if callable(on_print_option):
						on_print_option(i, i == defaultindex, choices[i])
						printed = True
				if not printed:
					print((" " * indent_options) + f"{i+1}. {format_option(i)}")
			print()

			# input prompt
			inputprompt = ">"
			# if defaultindex and type(defaultindex) is int:
			# 	inputprompt = f"[{defaultindex+1}] > "
			selection = cls.input_int(inputprompt, (defaultindex+1), within=[1, len(choices)], allow_ctrlc=True)
			if selection:
				running = False
				return choices[selection-1]
			else:
				running = False
				return defaultindex
	# end numbermenu()

	# presents a menu, given a list of options
	# 
	# up/down - scroll up and down in the menu
	# enter/return - return the selected element from choices
	# 
	# choices => list of strings, or of dicts representing objects
	# 	format_str and format_fields allow for specifying which object properties
	# 	are shown when printing menu options
	# 
	# hotkeys => dict of keyname -> defined return value
	# 	if a hotkey is pressed, the menu will return the value defined in the hotkey map as a tuple:
	# 		(selection_index when the key was pressed, the defined response, the key)
	# 		
	# on_selection_changed -> None, or a callback function invoked when the selected index within the menu changes
	# 	def selection_changed(index: int):
	# 	... where index = the newly-selected index within the list of choices
	# 
	# on_print_option -> None, or function that overrides the printing of menu options
	#   def on_print_option(index: int, highlighted: bool, opt: object):
	#   ... where index = the index of the option being printed
	#   	      highlighted = whether this index is the selected one
	#   	      opt = choices[index]
	#   	      marked: bool (only when multiselect == True)
	# TODO:
	# - number_selection: allow user to press a number key to select a menu options
	# 	(only works for 10 choices or less?) 
	# 
	# NEEDS FURTHER TESTING BUT WORKS:
	# - multiselection - would need a hotkey (maybe space?) to mark/unmark items
	# 	- show selected items in another color? with a prefix? print checkboxes?      
	# 	- returns:
	# 		on option(s) selected:
	# 			a list of the selected options
	# 			OR if return_index is True, return a list of the indices of the marked items
	# 		on hotkey pressed:
	# 			a tuple:
	# 				(option that is highlighted when hotkey is pressed, the defined hotkey response, the key, [list of selected indices])
	@classmethod
	def menu(cls, prompt, choices, defaultvalue=None, *, title="Select an option", format_str="%s", format_fields=None, initial_index=0, raise_on_invalid_initial_index=True, allow_ctrlc=True, instructions=None, return_index=False, show_pages=True, show_selection=True, clear_on_finish=True, hotkeys={}, on_selection_changed=None, on_print_option=None, quit_keys=["x"], number_selection=False, multiselect=False, titlebar_bg="gray", titlebar_fg="black", bgcolor=None, disable_enter_action=False):
		prompt_lines = prompt.split("\n")

		if type(initial_index) is int:
			if initial_index >= len(choices) or initial_index < 0:
				if raise_on_invalid_initial_index:
					raise ValueError("initial index is out-of-range: %d (len(choices) = %d)" % (initial_index, len(choices)))

		running = True
		multiselect_states = [0 for _ in choices]

		selection_index = 0
		if multiselect:
			if type(initial_index) is list:
				# list of pre-selected indices
				for i in initial_index:
					multiselect_states[i] = 1
		else:
			# initial_index: int
			selection_index = initial_index
		page_number = 1

		choices = [c for c in choices if c is not None]

		if instructions is None:
			if multiselect:
				instructions = [
					"Use UP and DOWN to select an option",
					"Press SPACE to mark/unmark an item",
					"Press X to cancel",
					"===================================="
				]
			else:
				instructions = [
					"Use UP and DOWN to select an option",
					"Press X to cancel",
					"===================================="
				]

		prompt_lines.append("")
		for i in instructions:
			prompt_lines.append(i)
		
		def format_option(i, prefix=""):
			opt = choices[i]
			ms_state = bool(multiselect_states[i])

			if type(opt) is str:
				if multiselect:
					return f"[{'X' if ms_state else ' '}] {prefix + opt}"
				return prefix + opt
			#elif type(opt) is dict:
			else:
				if format_fields is None or format_fields == []:
					if multiselect:
						return f"[{'X' if ms_state else ' '}] {prefix + str(opt)}"
					return prefix + str(opt)

				values = []

				for f in format_fields:
					values.append(opt[f])

				if multiselect:
					return f"[{'X' if ms_state else ' '}] {prefix + (format_str % tuple(values))}"
				return prefix + (format_str % tuple(values))

		# enable better keyboard support if pynput is installed
		# if PYNPUT_DETECTED:
		# 	def pyn_on_press(key):
		# 		try:
		# 			# try detecting letter keys here
		# 			_ = key.char
		# 			pass
		# 		except AttributeError as e:
		# 			match key:
		# 				case Key.down

		# 	def pyn_on_release(key):
		# 		pass

		while running:
			termwidth, termheight = shutil.get_terminal_size((80, 20))

			cls.screen(title, bgcolor=bgcolor, barcolor=titlebar_bg, textcolor=titlebar_fg)
			for p in prompt_lines:
				print(p)

			if multiselect:
				cls.print_labelled("Current selection", f"{sum(multiselect_states)} selected of {len(choices)}")
			else:
				cls.print_labelled("Current selection", format_option(selection_index))
			print()

			# enforce minimum of two options shown at a time
			page_length = termheight - (len(prompt_lines) + 4)
			if page_length < 2:
				page_length = 2

			page_number = max(math.floor(selection_index / page_length), 0)
			max_pages = math.ceil(len(choices) / page_length)

			if show_selection:
				cls.print_msg("%d of %d " % (selection_index + 1, len(choices)), end="")
			if show_pages:
				cls.print_warning(f"{" / " if show_selection else ""}Page {page_number+1} of {max_pages}")
			else:
				print()

			start_list_index = ((page_number) * page_length)

			for i in range(page_length):
				loc_i = start_list_index + i
				if loc_i >= len(choices):
					continue

				# def on_print_option(index: int, highlighted: bool, opt: object):
				if on_print_option:
					if callable(on_print_option):
						if multiselect:
							on_print_option(loc_i, loc_i == selection_index, choices[loc_i], bool(multiselect_states[loc_i]))
						else:
							on_print_option(loc_i, loc_i == selection_index, choices[loc_i])
				else:
					if loc_i == selection_index:
						cls.print_highlighted(format_option(loc_i, "  "))
					else:
						cls.print(format_option(loc_i, "  "))

			handled = False
			while not handled:
				time.sleep(0.05)
				key = cls.get_keypress()
				handled = True

				if key == "up":
					selection_index -= 1
					if selection_index < 0:
						selection_index = len(choices) - 1
					else:
						if callable(on_selection_changed):
							on_selection_changed(selection_index)
				elif key == "down":
					selection_index += 1
					if selection_index >= len(choices):
						selection_index = 0
					else:
						if callable(on_selection_changed):
							on_selection_changed(selection_index)
				elif key == "pgup":
					selection_index -= page_length
					if selection_index < 0:
						selection_index = len(choices) - 1
					else:
						if callable(on_selection_changed):
							on_selection_changed(selection_index)
				elif key == "pgdown":
					selection_index += page_length
					if selection_index >= len(choices):
						selection_index = 0
					else:
						if callable(on_selection_changed):
							on_selection_changed(selection_index)
				elif key == "home":
					selection_index = 0
					if callable(on_selection_changed):
						on_selection_changed(selection_index)
				elif key == "end":
					selection_index = len(choices) - 1
					if callable(on_selection_changed):
						on_selection_changed(selection_index)
				elif key == "space" and multiselect:
					multiselect_states[selection_index] = not multiselect_states[selection_index]
					if callable(on_selection_changed):
						on_selection_changed(selection_index)
				elif key in ["enter", "return"] and not disable_enter_action:
					running = False
					if clear_on_finish:
						cls.clear()
						print()
					if multiselect:
						indices = [i for i in range(len(choices)) if bool(multiselect_states[i])]
						if return_index:
							return indices
						else:
							return [choices[i] for i in indices]
					else:
						if return_index:
							return selection_index
						else:
							return choices[selection_index]
				elif key in hotkeys:
					running = False
					if clear_on_finish:
						cls.clear()
						print()
					if multiselect:
						return (selection_index, hotkeys[key], key, [i for i in range(len(choices)) if bool(multiselect_states[i])])
					else:
						return (selection_index, hotkeys[key], key)
				elif key in quit_keys or (key == "ctrl-c" and allow_ctrlc):
					running = False
					if clear_on_finish:
						cls.clear()
						print()
					return defaultvalue
				else:
					handled = False
	# end menu()
	
	@classmethod
	def filebrowser(cls, title, prompt, *, start_dir=None, folder_select=False, show_files_in_folder_select=False, allow_mkdir=False, enter_on_mkdir=True):
		if start_dir is None:
			start_dir = cls.getcwd()

		def normalize_path(p):
			return p.replace("\\", "/")

		# def on_print_option(index: int, highlighted: bool, opt: object):
		def print_option(index, highlighted, opt):
			if show_files_in_folder_select and not opt["isdir"]:
				if highlighted:
					cls.print("  " + cls._color(bg="gray", fg="red") + opt["display"] + cls._reset())
				else:
					cls.print_dim("  " + opt["display"])
			else:
				if highlighted:
					cls.print_highlighted("  " + opt["display"])
				else:
					cls.print("  " + opt["display"])

		def go_up(fromdir):
			if normalize_path(fromdir).endswith(":/"):
				if platform.system() == "Windows":
					return "WINDOWS_ROOT"
				else:
					return "/"

			updir = "/".join(normalize_path(fromdir).split("/")[:-1])
			# print("upped '%s' to '%s'" % (fromdir, updir))
			# cls.pause()
			if updir == "":
				# at the root of the file tree
				# 
				# on windows, need to show a list of drives (somehow)
				# anything else, return "/"
				if platform.system() == "Windows":
					return "WINDOWS_ROOT"
				else:
					return "/"

			return updir

		# if current selection is not a folder, disable the enter-folder hotkey
		def selection_changed(index):
			if not choices[index]["isdir"]:
				if "right" in hotkeys:
					del hotkeys["right"]
			else:
				if "right" not in hotkeys:
					hotkeys["right"] = "cd"

		running = True
		current_dir = normalize_path(start_dir)

		hotkeys = {
			"backspace": "up-dir",
			"left": "up-dir",
			"c": "confirm",
			"right": "cd"
		}

		instructions = [
			"Use UP and DOWN to change your selection",
			"Press LEFT to go up a folder",
			"Press RIGHT to enter a directory",
			"Press ENTER to confirm, or X to cancel"
		]

		instructions.append("")
		instructions.append(cls.fgcolors["yellow"] + "Current path: " + cls._reset() + current_dir)
		if allow_mkdir:
			hotkeys["n"] = "mkdir"
			instructions.append("Press N to create a new folder in the current location")

		initial_index = 0
		index_tree = []
		first_iter = True

		while running:
			# update current path display
			if current_dir[-1] == ":":
				current_dir += "/"
				index_tree.clear()

			for i in range(len(instructions)):
				if instructions[i].startswith(cls.fgcolors["yellow"] + "Current path:"):
					instructions[i] = cls.fgcolors["yellow"] + "Current path: " + cls._reset() + current_dir.replace("WINDOWS_ROOT", "This PC")

			choices = []
			# print("current_dir = " + current_dir)
			# cls.pause()
			if first_iter and folder_select:
				current_dir = normalize_path(go_up(current_dir))

			if current_dir == "WINDOWS_ROOT":
				current_dir = "This PC"

				for d in os.listdrives():
					choices.append({
						"display": "[DIR] " + d,
						"path": d,
						"isdir": True
					})

				# can't make folders here
				if allow_mkdir and "n" in hotkeys:
					del hotkeys["n"]
					instructions[-1] = ""
			else:
				if allow_mkdir and "n" not in hotkeys:
					hotkeys["n"] = "mkdir"
					instructions[-1] = "Press N to create a new folder in the current location"

				# get contents of current path
				contents = [f for f in os.listdir(current_dir) if not folder_select or (folder_select and show_files_in_folder_select) or os.path.isdir(os.path.join(current_dir, f))]

				# build choices menu
				files = []
				folders = []
				for f in contents:
					fullpath = os.path.join(current_dir, f)
					if os.path.isdir(fullpath):
						folders.append({
							"display": "[DIR] " + f,
							"path": fullpath,
							"isdir": True
						})
					elif os.path.isfile(fullpath):
						files.append({
							"display": f,
							"path": fullpath,
							"isdir": False
						})

				folders.sort(key=lambda d: d["display"])
				files.sort(key=lambda f: f["display"])

				choices = list(chain(folders, files))

				if current_dir != "/":
					# build the up-dir choice
					up_path = go_up(current_dir)
					choices.insert(0, {
						"display": "[DIR] ..",
						"path": up_path,
						"isdir": True,
						"special": True
					})

				if first_iter and folder_select:
					initial_selection = next((item for item in choices if item["path"] == start_dir), None)
					if initial_selection:
						initial_index = choices.index(initial_selection)

			if initial_index >= len(choices):
				initial_index = 0
				index_tree.clear()

			# show the selection menu
			selection = cls.menu(prompt, choices, title=title, instructions=instructions, hotkeys=hotkeys, format_str="%s", format_fields=["display"], on_selection_changed=selection_changed, on_print_option=print_option, initial_index=initial_index)
			# print(selection)
			# cls.pause()
			
			if type(selection) is tuple:
				# (index, response, key)
				# hotkey was pressed
				match selection[1]:
					case "up-dir":
						current_dir = normalize_path(go_up(current_dir))
						if len(index_tree) > 0:
							initial_index = index_tree.pop()
						else:
							initial_index = 0
						first_iter = False
					case "cd":
						if os.path.isdir(normalize_path(choices[selection[0]]["path"])):
							current_dir = normalize_path(choices[selection[0]]["path"])
							index_tree.append(selection[0])
						first_iter = False
					case "confirm":
						npath = normalize_path(choices[selection[0]]["path"])
						if folder_select:
							# make sure we have a folder and not a file
							if os.path.isdir(npath):
								running = False
								return npath
							else:
								initial_index = selection[0]
								cls.bell()
						else:
							# make sure we have a file and not a folder
							if os.path.isfile(npath):
								running = False
								return npath
							else:
								initial_index = selection[0]
								cls.bell()
					case "mkdir":
						cls.screen("Create new folder")
						cls.print_msg("Current path: ", end="")
						print(current_dir)
						print()
						if resp := cls.input("Enter name of new folder: ", allow_ctrlc=True):
							if resp.strip() == "":
								continue

							try:
								os.mkdir(os.path.join(current_dir, resp))
								if enter_on_mkdir:
									current_dir = normalize_path(os.path.join(current_dir, resp))
									index_tree.append(0)
									first_iter = False
							except Exception as e:
								cls.print_error(str(e))
								cls.pause()
			elif type(selection) is dict:
				# result selected...?
				if os.path.isdir(selection["path"]):
					if "special" in selection:
						# up-dir
						current_dir = normalize_path(selection["path"])
						first_iter = False
					else:
						if not folder_select:
							initial_index = choices.index(selection)
							cls.bell()
						else:
							running = False
							return normalize_path(selection["path"])	
						# current_dir = normalize_path(selection["path"])
						# if not selection["special"]:
						# 	index_tree.append(choices.index(selection))
				elif os.path.isfile(selection["path"]):
					if folder_select:
						# invalid selection - can't select file in folder mode
						initial_index = choices.index(selection)
						cls.bell()
					else:
						running = False
						return normalize_path(selection["path"])
				elif selection["special"]:
					current_dir = selection["path"]
			else:
				# cancelled
				running = False
				return None
	# end filebrowser()
	 
	@classmethod
	def modalalert(cls, title, text, *, buttons=None, default_button=0, background_title="", bgtitle_bg="gray", bgtitle_fg="black", background_color="blue", titlebar_fg="red", titlebar_bg=None, box_color="gray", box_shadow="black", text_color="black", selected_bg="black", selected_fg="gray", button_fg="blue", box_style="double", allow_ctrlc=True, allow_esc_cancel=False, clear_on_finish=True, clear_on_start=True, no_user_interaction=False):
		if buttons is None:
			buttons = cls.ModalButtons.OK

		if clear_on_start:
			if background_title != "":
				cls.screen(background_title, bgcolor=background_color, barcolor=bgtitle_bg, textcolor=bgtitle_fg)
			else:
				cls.clear(bgcolor=background_color)

		termwidth, termheight = shutil.get_terminal_size((80, 20))

		# first, need to calculate dimensions of the box to show
		# TODO: with shorter text, make the box not be as wide as the terminal
		# 
		# msgbox_width = termwidth - 10
		msgbox_width = min(80, termwidth - 10)

		textlines = text.split("\n")
		msgbox_text = []
		for line in textlines:
			if line.strip() == "":
				msgbox_text.append("")
				continue

			wrapped = textwrap.wrap(line, msgbox_width-4)
			msgbox_text.extend(wrapped)
		# msgbox_text = textwrap.wrap(text, msgbox_width-4)
		# this gives an estimate of the box height
		# + 2 pad rows above and below
		# + 1 for title bar
		# + 1 for buttons
		# + 1 under buttons
		# + 1 for bottom frame
		msgbox_height = len(msgbox_text) + 6
		
		# upper-left coords are...
		# msgbox_x = 3
		msgbox_x = (math.floor(termwidth / 2) - math.floor(msgbox_width / 2)) - 2
		msgbox_y = math.floor(termheight / 2) - math.floor(msgbox_height / 2) 

		# TEST: draw the box
		for y in range(msgbox_height):
			st = ""
			for x in range(msgbox_width):
				if y == 0:
					if x == 0:
						st += cls.boxchars[box_style]["top"][0]
					elif x == msgbox_width - 1:
						st += cls.boxchars[box_style]["top"][2]
					else:
						st += cls.boxchars[box_style]["top"][1]
				elif y == msgbox_height - 1:
					if x == 0:
						st += cls.boxchars[box_style]["bottom"][0]
					elif x == msgbox_width - 1:
						st += cls.boxchars[box_style]["bottom"][2]
					else:
						st += cls.boxchars[box_style]["bottom"][1]
				else:
					if x == 0:
						st += cls.boxchars[box_style]["middle"][0]
					elif x == msgbox_width - 1:
						st += cls.boxchars[box_style]["middle"][2]
					else:
						st += cls.boxchars[box_style]["middle"][1]
			if y > 0:
				st += cls._color(fg=box_shadow) + cls.boxchars[box_style]["shadow"][3]

			stpos = cls.cursor_pos(msgbox_x+3, msgbox_y + y, returncode=True)
			cls.print(stpos + st, color=text_color, bgcolor=box_color, word_wrap=False)
			# print("\033[5;10HTEST")
		stpos = cls.cursor_pos(msgbox_x+4, msgbox_y + msgbox_height, returncode=True)
		cls.print(stpos + (cls.boxchars[box_style]["shadow"][3] * msgbox_width), color=box_shadow, bgcolor=background_color, word_wrap=False)

		# draw the title bar
		titlepos = cls.cursor_pos(msgbox_x + 5, msgbox_y, returncode=True)
		cls.print(titlepos + f" {title} ", color=titlebar_fg, bgcolor=box_color, word_wrap=False)

		# draw the alert text
		dy = 0
		for line in msgbox_text:
			txtpos = cls.cursor_pos(msgbox_x + 5, msgbox_y + 2 + dy, returncode=True)
			cls.print(txtpos + line, color=text_color, bgcolor=box_color, end="", word_wrap=False)
			dy += 1

		# compute the button draw location
		button_dims = [] # dimensions of buttons

		for button in buttons:
			button_dims.append(len(button["label"]) + 3)

		buttonrow_width = sum(button_dims)
		buttonrow_x = math.floor(termwidth / 2) - math.floor(buttonrow_width / 2)
		buttonrow_y = msgbox_y + msgbox_height - 2

		if no_user_interaction:
			# ignore printing the buttons; the dialog has been drawn, so just return here
			# useful for showing a status popup for a long-running operation
			# 
			# reset the cursor and return
			print(cls._reset())
			return None

		# input loop starts here
		running = True
		button_active = default_button # selected index
		while running:
			# draw the buttons
			buttonrow_x = math.floor(termwidth / 2) - math.floor(buttonrow_width / 2)
			buttonrow_txt = ""
			buttonrow_pos = cls.cursor_pos(buttonrow_x, buttonrow_y, returncode=True)
			for i in range(len(buttons)):
				button = buttons[i]
				if i == button_active:
					# check for color overrides
					clr_activebg = selected_bg
					clr_activefg = selected_fg
					if "activebg" in button:
						clr_activebg = button["activebg"]
					if "activefg" in button:
						clr_activefg = button["activefg"]

					buttonrow_txt += cls.bgcolors[clr_activebg] + cls.fgcolors[clr_activefg] + ">" + button["label"] + "<" + cls.bgcolors[box_color]
				else:
					buttonrow_txt += cls.bgcolors[box_color] + cls.fgcolors[button_fg] + " " + cls.mode["underline"] + button["label"] + cls.modereset["underline"] + " " + cls.bgcolors[box_color]

				buttonrow_txt += " "
			cls.print(buttonrow_pos + buttonrow_txt, word_wrap=False)
			cls.print(cls._reset(), end="", word_wrap=False)

			# wait for input: left and right, or enter to select
			handled = False
			while not handled:
				time.sleep(0.05)
				key = cls.get_keypress()

				if key == "left":
					button_active -= 1
					if button_active < 0:
						button_active = 0
					handled = True
					# print(cls.cursor_home(returncode=True) + str(button_active), end="")
				elif key == "right":
					button_active += 1
					if button_active >= len(buttons):
						button_active = len(buttons) - 1
					handled = True
					# print(cls.cursor_home(returncode=True) + str(button_active), end="")
				elif key in ["enter", "return"]:
					if clear_on_finish:
						print(cls._reset())
						cls.clear()

					handled = True
					running = False
					thebutton = buttons[button_active]
					retvalue = button_active
					if "value" in thebutton:
						retvalue = thebutton["value"]

					if "callback" in thebutton:
						if callable(thebutton["callback"]):
							thebutton["callback"](retvalue)
					return retvalue
				elif key == "esc" and allow_esc_cancel:
					handled = True
					running = False
					return False
				elif key == "ctrl-c":
					handled = True
					running = False
					if allow_ctrlc:
						return None
					else:
						raise KeyboardInterrupt()
	# end modalalert()
	
	# Text file reader that supports sections
	# (help.txt format should be like that used on asset_intake_v3)
	# 
	@classmethod
	def textreader(cls, title, filename, *, background_color=None, text_color=None, titlebar_bg="gray", titlebar_fg="black", allow_ctrlc=True, clear_on_finish=True, clear_on_start=True, raise_on_file_error=False, replacements={}, gotosection=0, theme=None, use_colors=True, use_pageupdown=True, use_homeend_scrolling=False, reverse_lines=False):
		running = True

		if not theme:
			theme = {}

		# indices
		section = gotosection
		lineindex = 0

		# text content as lines
		helpcontent = []

		try:
			with open(filename, "r") as f:
				helptext = f.read()

			helpsections = helptext.split("$SECTION$\n")

			if reverse_lines:
				for i in range(len(helpsections)):
					helpsections[i] = "\n".join(helpsections[i].split("\n")[::-1])

			# print(len(helpsections))
			# cls.pause()
		except FileNotFoundError as e:
			helpcontent = [f"Uh oh!  The requested text file couldn't be found or loaded properly.  You're on your own, buddy.\n\nFile not found: {filename}"]
			helpsections = [f"Uh oh!  The requested text file couldn't be found or loaded properly.  You're on your own, buddy.\n\nFile not found: {filename}"]
			if raise_on_file_error:
				raise e
		
		while running:
			cls.screen(title, bgcolor=background_color, barcolor=titlebar_bg, textcolor=titlebar_fg)
			termwidth, termheight = shutil.get_terminal_size((80, 20)) 

			cls.print("LEFT/RIGHT - change section | UP/DOWN - scroll on page", color="green", bgcolor=background_color, word_wrap=False)
			if not use_homeend_scrolling:
				cls.print("HOME - go to Beginning of document", color="green", bgcolor=background_color, word_wrap=False)
			else:
				pass
			if use_pageupdown:
				pass
			cls.print("Press Q or CTRL+C to return", color="green", bgcolor=background_color, word_wrap=False)
			cls.print(("=" * (termwidth - 2)), color=text_color, bgcolor=background_color, word_wrap=False)

			# 7 lines reserved for header bar and key labels
			# +1 on the bottom
			
			helpcontent = []
			for line in helpsections[section].split("\n"):
				if len(line.lstrip()) > termwidth-2:
					i = 0
					for l in textwrap.wrap(line, termwidth-2):
						# if original line started with a formatting mark, need to retain that!
						if line.lstrip()[0:3] in ["== ", "** ", "## ", "!! ", "~~ ", ".. "] and i > 0:
							helpcontent.append(line.lstrip()[0:3] + l)
						else:
							helpcontent.append(l)

						i += 1
				else:
					helpcontent.append(line)
			# helpcontent = textwrap.wrap(helpsections[section], termwidth)
			
			# max lines that can be shown: termheight - 9
			maxlines = termheight - 9
			showthis = helpcontent

			if len(helpcontent) > maxlines:
				maxscroll = len(helpcontent) - maxlines

				# need to be able to scroll here
				upper = lineindex + maxlines
				if upper > len(helpcontent):
					upper = len(helpcontent)

				showthis = helpcontent[lineindex:upper]
				# print("lineindex: %d / maxlines: %d / len(helpcontent): %d" % (lineindex, maxlines, len(helpcontent)))
				# cls.print_dim("Scroll: %d/%d" % (lineindex, maxscroll))
				
				if section > 0:
					cls.print_dim("Section %d / Scroll: %d/%d" % (section, lineindex, maxscroll))
				else:
					cls.print_dim("Contents / Scroll: %d/%d" % (lineindex, maxscroll))
			else:
				# can print it all and be fine
				if section > 0:
					cls.print_dim("Section %d of %d" % (section, len(helpsections) - 1))
				else:
					# cls.print_dim("Section %d of %d" % (section, len(helpsections) - 1))
					print()

			# print(helpcontent)
			# print(showthis)
			# cls.pause()
			for s in showthis:
				strn = s
				for rkey in replacements:
					strn = s.replace(f"{{{rkey}}}", replacements[rkey])
				# strn = s.replace("{app_name}", app_name)
				if strn.lstrip()[0:3] == "== ": # header line
					cls.print(strn.lstrip()[3:], color="lightblue", bgcolor=background_color, word_wrap=False)
				elif strn.lstrip()[0:3] == "** ": # warning line
					cls.print(strn.lstrip()[3:], color="yellow", bgcolor=background_color, word_wrap=False)
				elif strn.lstrip()[0:3] == "## ": # green line
					cls.print(strn.lstrip()[3:], color="green", bgcolor=background_color, word_wrap=False)
				elif strn.lstrip()[0:3] == "!! ": # red line
					cls.print(strn.lstrip()[3:], color="lightred", bgcolor=background_color, word_wrap=False)
				elif strn.lstrip()[0:3] == "~~ ": # purple line
					cls.print(strn.lstrip()[3:], color="magenta", bgcolor=background_color, word_wrap=False)
				elif strn.lstrip()[0:3] == ".. ": # dark gray line
					cls.print(strn.lstrip()[3:], color="darkgray", bgcolor=background_color, word_wrap=False)
				else:
					cls.print(strn.replace("\t", "    "), color=text_color or "gray", bgcolor=background_color, word_wrap=False)
			
			handled = False
			while not handled:
				handled = False
				time.sleep(0.05)
				# key = cls.get_keypress()
				keycode = Terminal.get_keycode()
				key = Terminal._code_to_keyname(keycode)

				# print(f"{keycode} => {key}")
				match key:
					case "q" | "esc" | "backspace":
						handled = True
						running = False
					case "ctrl-c":
						handled = True
						if allow_ctrlc:
							running = False
						else:
							raise KeyboardInterrupt()
					case "left":
						handled = True
						section = section - 1
						if section < 0:
							section = 0
						lineindex = 0
					case "right":
						handled = True
						section = section + 1
						if section >= len(helpsections):
							section = len(helpsections) - 1
						lineindex = 0
					case "up":
						handled = True
						lineindex = lineindex - 1
						if lineindex < 0:
							lineindex = 0
					case "down":
						handled = True
						lineindex += 1
						if lineindex + maxlines > len(helpcontent):
							lineindex = len(helpcontent) - maxlines
					case "home":
						handled = True
						lineindex = 0
						if not use_homeend_scrolling:
							section = 0
					case "end":
						if use_homeend_scrolling:
							handled = True
							lineindex = len(helpcontent) - maxlines
					case "pgup":
						if use_pageupdown:
							handled = True
							lineindex -= maxlines
							if lineindex < 0:
								lineindex = 0
							print(f"maxlines: {maxlines} / lineindex: {lineindex}")
					case "pgdown":
						if use_pageupdown:
							handled = True
							lineindex += maxlines
							if lineindex + maxlines > len(helpcontent):
								lineindex = len(helpcontent) - maxlines
							print(f"maxlines: {maxlines} / lineindex: {lineindex}")
					case "+": # macOS-compatible alternative for PgUp
						if use_pageupdown and platform.system() == "Darwin":
							handled = True
							lineindex -= maxlines
							if lineindex < 0:
								lineindex = 0
							print(f"maxlines: {maxlines} / lineindex: {lineindex}")
					case "+":# macOS-compatible alternative for PgDown
						if use_pageupdown and platform.system() == "Darwin":
							handled = True
							lineindex += maxlines
							if lineindex + maxlines > len(helpcontent):
								lineindex = len(helpcontent) - maxlines
							print(f"maxlines: {maxlines} / lineindex: {lineindex}")

			# end while (key input loop)
		# end while (text viewer loop)
		# 
		if clear_on_finish:
			cls.clear()
	# end textreader()

	# preset button definitions for modals
	class ModalButtons:
		OK = [
			{
				"label": "OK",
				"value": True
			}
		]

		OKCANCEL = [
			{
				"label": "OK",
				"value": True
			},
			{
				"label": "Cancel",
				"value": False
			}
		]

		YESNO = [
			{
				"label": "Yes",
				"value": True
			},
			{
				"label": "No",
				"value": False
			}
		]

		YESNOCANCEL = [
			{
				"label": "Yes",
				"value": True
			},
			{
				"label": "No",
				"value": False
			},
			{
				"label": "Cancel",
				"value": None
			}
		]
	# end class ModalButtons
	
	# ==========================================================
	# keyboard input helper functions
	# 
	# https://stackoverflow.com/a/70664652
	# cross-platform key detection
	
	# macOS and Windows have different codes for the arrow keys
	# TODO: KEYCODES ARE DIFFERENT WITH AND WITHOUT CAPSLOCK
	# 
	_keys = {
		"up":           ["\\x48", "\\xe0\\x48", "\\x1b\\x5b\\x41"],
		"down":         ["\\x50", "\\xe0\\x50", "\\x1b\\x5b\\x42"],
		"left":         ["\\x4b", "\\xe0\\x4b", "\\x1b\\x5b\\x44"],
		"right":        ["\\x4d", "\\xe0\\x4d", "\\x1b\\x5b\\x43"],
		"a":            "\\x61",
		"b":            "\\x62",
		"c":            "\\x63",
		"d":            "\\x64",
		"e":            "\\x65",
		"f":            "\\x66",
		"g":            "\\x67",
		"h":            "\\x68",
		"i":            "\\x69",
		"j":            "\\x6a",
		"k":            "\\x6b",
		"l":            "\\x6c",
		"m":            "\\x6d",
		"n":            "\\x6e",
		"o":            "\\x6f",
		"p":            "\\x70",
		"q":            "\\x71",
		"r":            "\\x72",
		"s":            "\\x73",
		"t":            "\\x74",
		"u":            "\\x75",
		"v":            "\\x76",
		"w":            "\\x77",
		"x":            "\\x78",
		"y":            "\\x79",
		"z":            "\\x7a",
		"1":            "\\x31",
		"2":            "\\x32",
		"3":            "\\x33",
		"4":            "\\x34",
		"5":            "\\x35",
		"6":            "\\x36",
		"7":            "\\x37",
		"8":            "\\x38",
		"9":            "\\x39",
		"0":            "\\x30",
		"space":		"\\x20",
		"backspace":    ["\\x08", "\\x7f"],
		"enter":        "\\xe0\\x0d",
		"return":       "\\x0d",
		"esc":          ["\\x1b", "\\xe0\\x1b"],
		"ins":          "\\xe0\\x52",
		"del":          "\\xe0\\x53",
		"pgup":         ["\\x49", "\\xe0\\x49"],
		"pgdown":       ["\\x51", "\\xe0\\x51"],
		"home":         "\\xe0\\x47",
		"end":          "\\xe0\\x4f",
		"ctrl-c":       "\\x03",
		"ctrl-z":       "\\x1a",
		"ctrl-x":       "\\x18",
		"ctrl-s":       "\\x13",
		"ctrl-a":       "\\x01",
		"ctrl-enter":   "\\x0a",
		"f1":           "\\x00\\x3b",
		"f2":           "\\x00\\x3c",
		"f3":           "\\x00\\x3d",
		"f4":           "\\x00\\x3e",
		"f5":           "\\x00\\x3f",
		"f6":           "\\x00\\x40",
		"f7":           "\\x00\\x41",
		"f8":           "\\x00\\x42",
		"f9":           "\\x00\\x43",
		"f10":          "\\x00\\x44",
		"f12":          "\\xe0\\x86",
		"~":			"\\x7e",
		"!":			"\\x21",
		"@":			"\\x40",
		"#":			"\\x23",
		"$":			"\\x24",
		"%":			"\\x25",
		"^":			"\\x5e",
		"&":			"\\x26",
		"*":			"\\x2a",
		"(":			"\\x28",
		")":			"\\x29",
		"-":			"\\x2d",
		"=":			"\\x3d",
		"_":			"\\x5f",
		"+":			"\\x2b"
	}

	@classmethod
	def _get_keystroke(cls):
		key = _next_input()
		while (len(key) <= _MAX_ESCAPE_SEQUENCE_LENGTH and
			   key in _ESCAPE_SEQUENCES[len(key)-1]):
			key += _next_input()
		return key

	@classmethod
	def _flush(cls):
		while _input_ready():
			_next_input()

	@classmethod
	def _print_key(cls) -> None:
		"""Print the key that was pressed
		
		Useful for debugging and figuring out keys.
		"""
		with _set_terminal_raw():
			cls._flush()
			print("\\x" + "\\x".join(map("{:02x}".format, map(ord, cls._get_keystroke()))))

	@classmethod
	def get_keycode(cls) -> None:
		with _set_terminal_raw():
			cls._flush()
			return "\\x" + "\\x".join(map("{:02x}".format, map(ord, cls._get_keystroke())))

	@classmethod
	def _code_to_keyname(cls, key):
		for k in cls._keys:
			v = cls._keys[k]
			if type(v) is list:
				for i in v:
					if key == i:
						return k
			else:
				if key == v:
					return k

		return "unmapped"

	@classmethod
	def get_keypress(cls) -> str:
		return cls._code_to_keyname(cls.get_keycode())

	@classmethod
	def wait_key(cls, key=None, *, pre_flush=False, post_flush=True) -> str:
		"""Wait for a specific key to be pressed.

		Args:
			key: The key to check for. If None, any key will do.
			pre_flush: If True, flush the input buffer before waiting for input.
			Useful in case you wish to ignore previously pressed keys.
			post_flush: If True (default), flush the input buffer after the key was
			found. Useful for ignoring multiple key-presses.
		
		Returns:
			The key that was pressed.
		"""
		with _set_terminal_raw():
			if pre_flush:
				cls._flush()

			if key is None:
				key = cls._get_keystroke()
				if post_flush:
					cls._flush()
				return key

			while cls._get_keystroke() != key:
				pass
			
			if post_flush:
				cls._flush()

			return key
	
	class util:
		def parse_int(strn, *, raise_exception=False, defaultvalue=None):
			try:
				return int(strn)
			except ValueError as e:
				if raise_exception:
					raise e

				if defaultvalue:
					return defaultvalue

				return False
	# end class Terminal.util
# end class Terminal

if __name__ == "__main__":
	Terminal.print_msg("It works!  Maybe...")
	Terminal.print_success("Try the tests!")
