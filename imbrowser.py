from itemmaster import Itemmasters
from jb93term import Terminal as term

import json, logging, math, os, platform, subprocess, sys, tempfile, zipfile
from base64 import b64decode
from datetime import date, datetime, timezone, timedelta
from functools import cache
from hashlib import file_digest
from shutil import copyfileobj
from typing import cast
from urllib.request import urlretrieve

_defpath = os.getcwd() # os.path.realpath(__file__)

APP_NAME="IMBrowser"
APP_VERSION="1.0"
APP_DATE=datetime.fromtimestamp(os.path.getmtime(sys.argv[0])).strftime("%Y-%m-%d %H:%M:%S")
DEBUG=True
LOG_TO_STDOUT=False

def normalize_path(path: str, /, *paths: Any) -> str: return (root := os.path.join(path.removeprefix("\"").removesuffix("\""), *map(str, paths)).replace("\\", "/")) + ("/" if os.path.isdir(root) else "")

SYSTEM_SHELLVALUE=False
SYSTEM_SHELLVALUE_FLIPPED=False # becomes True if the flag is dynamically changed to address crashing
def system(*args: str, **kwargs: Any) -> str | None:
	global SYSTEM_SHELLVALUE, SYSTEM_SHELLVALUE_FLIPPED
	try:
		logger.debug("[*] Running %s", args)

		if "timeout" not in kwargs:
			kwargs["timeout"] = 30
		# logger.debug("   with timeout %d" % kwargs["timeout"])

		# print(args)
		process = subprocess.run(args, check=True, capture_output=True, shell=SYSTEM_SHELLVALUE, cwd=os.getcwd(), text=True, encoding="utf-8", **kwargs)
		output = process.stdout or process.stderr
		return output.strip() if output else None
	except subprocess.CalledProcessError as exception:
		output = exception.stdout or exception.stderr
		logger.debug("[!] Command %s exited abnormally with %d; ignoring: %s", args, exception.returncode, output.strip() if output else None)
		return output.strip() if output else None
	except subprocess.TimeoutExpired as exception:
		logger.debug("[!] Command timed out after %f second(s); ignoring: %s", kwargs.get("timeout", "?"), args)
		return None
	except FileNotFoundError as exception:
		# try flipping the Shell value and try again
		if not SYSTEM_SHELLVALUE_FLIPPED:
			SYSTEM_SHELLVALUE = not SYSTEM_SHELLVALUE
			SYSTEM_SHELLVALUE_FLIPPED = True
			return system(*args, **kwargs)
		else:
			# already tried this, crash out
			term.print_error("* Something went wrong trying to run subprocesses on your system :(")
			term.print_error(str(exception))
			raise exception

@cache
def _qrencode(value: str, *, return_string: bool = False) -> str | None:
	output = system(os.path.join(IMBrowser.LIBQRENCODE_PATH, IMBrowser.LIBQRENCODE_EXE), "--type", "ANSI", "--margin=1", value)
	if return_string:
		return output
	print(output)

class IMBrowser:
	_im = None

	PLATFORM: str
	LIBQRENCODE_PATH: str
	LIBQRENCODE_WINDOWS: str = "https://master.dl.sourceforge.net/project/qrencode-for-windows/QREncode-4.1.1_Win32(static).zip?viasf=1:eb3afc0f87bf9f1c9af143fcd205c29c07f605bf"
	LIBQRENCODE_EXE: st
	PROGRAM_PATH: str = normalize_path(os.path.expanduser("~"), "imbrowser")
	LIBRARY_PATH: str = normalize_path(PROGRAM_PATH, "libs")

	@classmethod
	def initialize(cls, *, imfile=None):
		if not imfile:
			imfile = os.path.join(_defpath, "itemmasters.csv")
		cls._im = Itemmasters(filename=imfile)

		cls.prepare_runtime()

	@classmethod
	def prepare_runtime(cls):
		term.print_warning("* Verifying dependencies...")

		cls.PLATFORM = platform.system()

		match cls.PLATFORM:
			case "Windows":
				cls.LIBQRENCODE_PATH = normalize_path(cls.LIBRARY_PATH, "libqrencode")
				cls.LIBQRENCODE_EXE = "qrencode.exe"

				if not os.path.exists(cls.LIBQRENCODE_PATH):
					os.makedirs(cls.LIBQRENCODE_PATH)
				if not os.path.isfile(normalize_path(cls.LIBQRENCODE_PATH, "qrencode.exe")):
					with open(path := urlretrieve(cls.LIBQRENCODE_WINDOWS[:cls.LIBQRENCODE_WINDOWS.rindex(":")])[0], "rb") as file:
						if file_digest(file, "sha1").hexdigest() == cls.LIBQRENCODE_WINDOWS[cls.LIBQRENCODE_WINDOWS.rindex(":") + 1:]:
							with zipfile.ZipFile(path) as archive, archive.open(archive.namelist()[-1]) as source, open(normalize_path(cls.LIBQRENCODE_PATH, "qrencode.exe"), "wb") as target:
								copyfileobj(source, target)
			case "Darwin":
				use_which = (which := system("which")) is not None and (which == "" or which.startswith("usage:"))
				use_which = True
				cls.LIBQRENCODE_PATH = "/usr/local/bin/"
				cls.LIBQRENCODE_EXE = "qrencode"

				libqrencode_path = cast(str, system("which", "qrencode"))

				if system("which", "qrencode") == "qrencode not found" if use_which else not os.path.isfile(normalize_path(cls.LIBQRENCODE_PATH, "qrencode")):
					system("brew", "install", "libqrencode")
				if use_which:
					cls.LIBQRENCODE_PATH = "/".join(str(system("which", "qrencode")).split("/")[:-1])
					cls.LIBQRENCODE_EXE = "qrencode"
			case "Linux":
				cls.LIBQRENCODE_PATH = "/usr/local/bin/"
				if not os.path.isfile(normalize_path(cls.LIBQRENCODE_PATH, "qrencode")):
					system("sudo", "apt-get", "install", "libqrencode", interactive=True)
			case _:
				raise RuntimeError("Unsupported platform: " + PLATFORM)

	@classmethod
	def main(cls):
		term.print_msg("ItemMaster Browser and QR code tool")
		term.print("Enter search terms to find an Itemmaster code")
		term.print("Use 2-char code to toggle device/item search:")
		term.print_labelled("  -d or +d", "Set device search to OFF or ON")
		term.print_labelled("  -i or +i", "Set accessory search to OFF or ON")
		term.print_labelled("  -? or -h", "View help and additional commands")
		term.print("Type 'exit' to quit")
		print()

		running = True

		search_devices = True
		search_items = True
		favor_code_matches = False

		while running:
			searchfor = term.input(f"[{'+' if search_devices else '-'}D{'+' if search_items else '-'}I] ? ").strip()
			if not searchfor or searchfor == "":
				continue

			if searchfor.lower() == "exit" or searchfor.lower() == "q":
				running = False
				continue

			if len(searchfor) == 2:
				match searchfor.lower():
					case "-d":
						search_devices = False
						term.print_labelled("  Search devices", "OFF")
						continue
					case "+d":
						search_devices = True
						term.print_labelled("  Search devices", "ON")
						continue
					case "-i":
						search_items = False
						term.print_labelled("  Search items", "OFF")
						continue
					case "+i":
						search_items = True
						term.print_labelled("  Search items", "ON")
						continue
					case "-?" | "-h":
						term.print_labelled("  -d or +d", "Set device search to OFF or ON")
						term.print_labelled("  -i or +i", "Set accessory search to OFF or ON")
						term.print_labelled("  -r", "Reload the itemmasters CSV data file", color="yellow") # TODO
						term.print_labelled("  -? or -h", "View help and additional commands")
						continue

			selected = cls._im.select(searchfor, search_devices=search_devices, search_items=search_items, favor_code_matches=favor_code_matches)
			if not selected:
				continue
			else:
				# print(selected)
				print()
				term.print_labelled(selected["itemnumber"], selected["description"])
				qrcode = _qrencode(selected["itemnumber"], return_string=True)
				print(qrcode)
				print()

if __name__ == "__main__":
	logger = logging.getLogger(__name__)
	# start the logger
	log_handlers = [
		logging.FileHandler("imbrowser-py.log", mode="w")
	]

	if LOG_TO_STDOUT:
		log_handlers.append(logging.StreamHandler())

	log_format = "%(asctime)s [%(module)s.%(funcName)s] [%(levelname)s] %(message)s"
	logging.basicConfig(
		encoding='utf-8',
		datefmt='%m/%d/%Y %H:%M:%S',
		format=log_format,
		handlers=log_handlers
	)
	logger.setLevel(logging.DEBUG if DEBUG else logging.INFO)
	logger.info(f"{APP_NAME} {APP_VERSION} - updated {APP_DATE}")

	IMBrowser.initialize()

	IMBrowser.main()