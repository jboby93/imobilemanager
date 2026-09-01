###
# imobilemanager.py
# author: jenna b., hao z.
# 
# power to open-source software
# 
# more info to come
# TODO:
# - all TODOs
# - dump device json info when detected, for debugging and review of connected devices
# 

import difflib, enum, json, logging, os, platform, random, re, shutil, subprocess, sys, tarfile, tempfile, textwrap, webbrowser, zipfile
from base64 import b64decode
from collections import defaultdict, namedtuple
from collections.abc import Collection, Container, Iterator, Mapping, MutableMapping, MutableSet, Set
from concurrent.futures import Executor, ThreadPoolExecutor, as_completed
from datetime import date, datetime, timezone, timedelta
from functools import cache, cached_property
from hashlib import file_digest
from itertools import batched, chain, zip_longest
from pprint import pformat
from shutil import copyfileobj
from time import sleep, strftime, time, localtime
from typing import cast, Any, Hashable
from urllib.request import urlretrieve
from xml.etree import ElementTree

# standard Terminal-related functions and helpers
from jb93term import Terminal as term
# ipsw.me API
from ipsw import IPSW, IPSWApp
# various data structures
from imm_defs import REPLCompleter, Carrier, Attribute, DomainKey, MobileApp, ChipID

APP_NAME="iMobileManager"
APP_VERSION="0.7 beta"
APP_DATE=datetime.fromtimestamp(os.path.getmtime(sys.argv[0])).strftime("%Y-%m-%d %H:%M:%S")
DEBUG=True
LOG_TO_STDOUT=False

def normalize_path(path: str, /, *paths: Any) -> str: return (root := os.path.join(path.removeprefix("\"").removesuffix("\""), *map(str, paths)).replace("\\", "/")) + ("/" if os.path.isdir(root) else "")

# Hao:
# there was a lot of fuckery that happened here lmao, some versions of system() were behaving differently based on the computer,
# not the OS. ex: shell has to be set to False on my personal macbook, but it needs to be True on the macbook air at work (otherwise it would give FileNotFoundErrors)
# 
# no clue why, so i added an auto-switch to flip the flag if it fails the first time and then crash out if it still doesn't work lmao
# ------------
SYSTEM_SHELLVALUE=False
SYSTEM_SHELLVALUE_FLIPPED=False # becomes True if the flag is dynamically changed to address crashing

# on success:
# 	=> (returncode: int, output: str)
# on CalledProcessError
#   => (returncode: int, output: str)
# on TimeoutError:
#   => (timeout: int, None)
def system2(*args, **kwargs):
	global SYSTEM_SHELLVALUE, SYSTEM_SHELLVALUE_FLIPPED
	try:
		logger.debug("[*] Running %s", args)

		if "timeout" not in kwargs:
			kwargs["timeout"] = 30
		if "cwd" not in kwargs:
			kwargs["cwd"] = IMobileDevice.PROGRAM_PATH
		# logger.debug("   with timeout %d" % kwargs["timeout"])

		# remove custom keyword args since subprocess.run() is a [redacted] and doesn't ignore unknown args
		subp_kwargs = {k: kwargs[k] for k in kwargs if k not in ["restore_job"]}

		# print(args)
		process = subprocess.run(args, check=True, capture_output=True, shell=SYSTEM_SHELLVALUE, text=True, encoding="utf-8", **subp_kwargs)
		output = process.stdout or process.stderr
		# return output.strip() if output else None
		return (0, output.strip() if output else None)
	except subprocess.CalledProcessError as exception:
		if "restore_job" in kwargs:
			logger.debug(f"[!] Restore command {args} exited abnormally with {exception.returncode}; please check the restore log for this device to find out what happened")
			return (exception.returncode, None)

		output = exception.stdout or exception.stderr
		logger.debug("[!] Command %s exited abnormally with %d; ignoring: %s", args, exception.returncode, output.strip() if output else None)
		# return output.strip() if output else None
		return (exception.returncode, output.strip() if output else None) 
	except subprocess.TimeoutExpired as exception:
		logger.debug("[!] Command timed out after %f second(s); ignoring: %s", kwargs.get("timeout", "?"), args)
		# return None
		return (kwargs.get("timeout", "?"), None)
	except FileNotFoundError as exception:
		# try flipping the Shell value and try again
		if not SYSTEM_SHELLVALUE_FLIPPED:
			SYSTEM_SHELLVALUE = not SYSTEM_SHELLVALUE
			SYSTEM_SHELLVALUE_FLIPPED = True
			# return system(*args, **kwargs)
			return system2(*args, **kwargs)
		else:
			# already tried this, crash out
			term.print_error("* Something went wrong trying to run subprocesses on your system :(")
			term.print_error(str(exception))
			raise exception

# STRICTLY for compatibility while remainder of codebase is updated for the new change
def system(*args: str, **kwargs: Any) -> str | None:
	# => (returncode: int, output: str | None)
	# return system2(*args, **kwargs)[1]
	return system2(*args, **kwargs)

# returns True if successful, False otherwise
def download_file(url, saveto, *, binary=False, pause_on_error=True, raise_on_fail=False) -> bool:
	try:
		with open(urlretrieve(url)[0], "rb" if binary else "r") as source, open(saveto, "wb" if binary else "w") as target:
			target.write(source.read())
		return True
	except Exception as e:
		term.print_error(f"* unable to download file: {str(e)}\n  URL: {url}\n\n  SAVE TO: {saveto}")
		if raise_on_fail:
			raise e
		if pause_on_error:
			term.pause()
	return False

@cache
def _qrencode(value: str, *, return_string: bool = False) -> str | None:
	rtn, output = system(os.path.join(IMobileDevice.LIBQRENCODE_PATH, IMobileDevice.LIBQRENCODE_EXE), "--type", "ANSI", "--margin=1", value)
	if return_string:
		return output
	print(output)

def _libimd(*args: str, **kwargs: str) -> str | None:
	kwargs["cwd"] = IMobileDevice.LIBIMOBILEDEVICE_PATH
	return system(*args, **kwargs)

def decode_plist(node: ElementTree.Element, /, *, decoders = {
	"string": lambda node: text.strip() if (text := node.text) else None,
	"data": lambda node: b64decode(text.strip()).decode(errors="ignore") if (text := node.text) else None,
	"integer": lambda node: int(node.text),
	"real": lambda node: float(node.text),
	"date": lambda node: date.fromisoformat(node.text),
	"true": lambda _: True,
	"false": lambda _: False,
	"plist": lambda node: {child.tag: decode_plist(child) for child in node},
	"dict": lambda node: {key.text: decode_plist(value) for (key, value) in batched(node, 2, strict=True) if key.text != "Status" or value.text != "Success"},
	"array": lambda node: [decode_plist(child) for child in node],
}) -> Any:
	return output[key] if isinstance(output := decoders.get(node.tag, ElementTree.tostring)(node), Mapping) and len(output) == 1 and (key := next(iter(output))) in decoders else output

# stores unique identifiers for a device in a common place
class DeviceID(Hashable):
	# list of DeviceIDs from seen devices
	_historycache = []

	@classmethod
	def was_id_seen(cls, deviceid):
		return deviceid in cls._historycache

	@classmethod
	def search_seen_ids(cls, *, serial=None, udid=None, ecid=None):
		if not serial and not udid and not ecid:
			raise ValueError("one of serial, udid, or ecid must be provided")

		compare = DeviceID(serial=serial, ecid=ecid, udid=udid, _cache=False)

		for h in cls._historycache:
			if h == compare:
				return h

		return None

	def __init__(self, *, serial=None, ecid=None, udid=None, chipid=None, _cache=True):
		self.serial = serial
		self.ecid = ecid
		self.udid = udid
		self.chipid = chipid

		if _cache:
			DeviceID._historycache.append(self)

	def __eq__(self, other):
		if self is other:
			return True

		if isinstance(other, type(self)):
			if self.serial and other.serial:
				if self.serial != other.serial:
					return False
			if self.ecid and other.ecid:
				if self.ecid != other.ecid:
					return False
			if self.udid and other.udid:
				if self.udid != other.udid:
					return False
			if self.chipid and other.chipid:
				if self.chipid != other.chipid:
					return False

			return True
		else:
			return False

	def __repr__(self):
		dict_repr = {
			"serial": self.serial,
			"ecid": self.ecid,
			"udid": self.udid,
			"chipid": self.chipid
		}

		return f"{self.__class__.__name__}({dict_repr})"

	def __hash__(self) -> int:
		return id(self)

	def _id(self):
		return self.serial_number or self.ecid

	# for debugging purposes (and because idevice_models.json is incomplete/there are edge cases where model numbers vary for the same product type)
	# returns: a model number that is guaranteed to be in the models dict
	@classmethod
	def match_model_record(cls, modelnumber, guesses={}, serial=None):
		if len(guesses) > 0:
			choices = [f"{key}: {guesses[key][1]} {guesses[key][2]} {guesses[key][3]}" for key in guesses]
			choices.append("00000: None of these")
			# start with the possible matches
			selection = term.menu("Does the connected device's model match any of these?", choices, title=f"Manually identify model '{modelnumber}' (S/N: {serial or 'n/a'})", quit_keys=["x", "q"])
			if selection:
				# print(selection)
				match_key = selection.split(":")[0]
				if match_key != "00000":
					model_record = IMobileDevice.models_dict[match_key]
					IMobileDevice.models_dict[modelnumber] = model_record
					try:
						with open(IMobileDevice.MODELS_DICT_FILE, "w") as f:
							f.write(json.dumps(IMobileDevice.models_dict, indent=4))
						logger.info(f"appended new device model record: {match_key} => {model_record}")
					except Exception as e:
						term.print_error(f"Couldn't save {IMobileDevice.MODELS_DICT_FILE} - {str(e)}")

					return match_key

		# user can search for the model
		# 
		return None

	@classmethod
	def store_model_record(cls, devinfo):
		if (modelnumber := devinfo["BaseModelNumber"]) and devinfo["ModelName"] and devinfo["ModelStorage"] and devinfo["ModelColor"] and devinfo["ModelReleaseDate"]:
			IMobileDevice.models_dict[modelnumber] = (model_record := [
				devinfo["ProductType"],
				devinfo["ModelName"],
				devinfo["ModelStorage"],
				devinfo["ModelColor"],
				devinfo["ModelReleaseDate"]
			])

			try:
				with open(IMobileDevice.MODELS_DICT_FILE, "w") as f:
					f.write(json.dumps(IMobileDevice.models_dict, indent=4))
				logger.info(f"appended new device model record: {modelnumber} => {model_record}")
				return True
			except Exception as e:
				term.print_error(f"Couldn't save {IMobileDevice.MODELS_DICT_FILE} - {str(e)}")
				return False
		return False
# end class DeviceID

# stores Device info and interaction/management functions
class Device(Mapping[str, Any]):
	def __init__(self, info=None, *, identifier=None, in_recovery=False):
		self._info = (info or {})

		if not identifier:
			identifier = DeviceID(serial=self.serial_number, ecid=self.ecid, udid=self.udid, chipid=self.chipid)

		self._id = identifier
		DeviceID._historycache.append(self._id)

		self._in_recovery = in_recovery
		self._is_recovering = False
		self._restore_protection = False
		self._connected = True

		self._domains = {}
		self._gasgauge = None
		self._storage = None
		self._battery = None
		self._mdm = None
		self._locale = None

		self._user_apps = None
		self._system_apps = None

		logger.debug("spawned Device with ID: %s" % identifier)

	# 	# To find the closest matching model, check that:
	#   # - at least three of the last four characters of the model number (e.g., M[123]N) matches;
	#   # - the prefix of the product type (e.g., [iPhone18],5) matches;
	#   # - the formal product name matches;
	#   # - and the storage capacity matches.
	#   
	#   DeviceColor value may also be used to pinpoint from multiple results...?
	#   IF it is an int (.isdigit() returns True), can use that as an index
	#   hao_idevice.py:1107: __model()
	#   
	#   returns True if success
	#   
	#   sets Model property values on self
	#   
	#   allow_corrections: bool
	#   - if True, when a device cannot be directly identified, can search for a template to duplicate and
	#     save back to the idevice_models.json list
	#     (mostly for dev purposes)
	def infer_model(self, *, allow_corrections=True) -> bool:
		logger.debug("identifying device with ECID %s" % self.ecid)
		if self.model_number:
			model_filter = [m for m in list(IMobileDevice.models_dict.keys()) if m[1:] == self.model_number[1:]]
			if len(model_filter) > 0:
				self._info["BaseModelNumber"] = model_filter[0]
				model = IMobileDevice.models_dict[model_filter[0]]

				self._info["ModelName"] = model[1]
				self._info["ModelStorage"] = model[2]
				self._info["ModelColor"] = model[3]
				self._info["ModelReleaseDate"] = model[-1] if model[-1] != model[3] else None

				logger.info("* model number '%s' is a(n) %s" % (self.model_number, self.model_name))
				return True
			else:
				logger.debug("no direct match for model number '%s' found, attempting inference" % self.model_number)

		if self.product_type:
			assumed = False

			product_name = IMobileDevice.get_name_for_product_type(self.product_type)

			# self.storage_capacity
			type_prefix = self.product_type.split(",")[0]

			possible_matches = {key: IMobileDevice.models_dict[key] for key in IMobileDevice.models_dict if (value := IMobileDevice.models_dict[key])[0] == self.product_type and value[2] == self.storage_capacity}
			# print(possible_matches)

			if allow_corrections and self.model_number:
				if (model_override := DeviceID.match_model_record(self.model_number, possible_matches, self.serial_number)):
					self._info["BaseModelNumber"] = model_override
					model = IMobileDevice.models_dict[model_override]
					self._info["ModelName"] = model[1]
					self._info["ModelStorage"] = model[2]
					self._info["ModelColor"] = model[3]
					self._info["ModelReleaseDate"] = model[-1] if model[-1] != model[3] else None

					logger.info("* device with ECID %s is (user-defined as) a(n) %s" % (self.ecid, model[1]))
					return True

			if isinstance(color := self[Attribute.COLOR], str) and color.isdigit() and ((c_index := int(color) - 1) < len((c_options := [model[3] for model in possible_matches.values()]))):
				color_name = c_options[c_index]
			else:
				if self.model_number:
					close_matches = difflib.get_close_matches(self.model_number, possible_matches.keys(), cutoff=0.5)
					if len(close_matches) > 0:
						# use the first option as a default
						color_name = possible_matches[close_matches[0]][3]
						# print(f"color name: {color_name}")
						assumed = True
				else:
					# ???
					logger.warning("* product_type '%s' has no model number????" % self.product_type)
					logger.warning("  using first of possible matches: %s" % possible_matches[0])
					color_name = possible_matches[0]
					assumed = True

			narrowed_matches = {key: possible_matches[key] for key in possible_matches if possible_matches[key][3] == color_name}
			# print(narrowed_matches)
			if len(narrowed_matches) == 0:
				# collect information manually
				term.screen(f"Identify device: {self.product_type} - {self.serial_number}")
				term.print_warning("This device can't be fully identified.  Please enter the needed information for this device and it will be stored for future use.")
				print()
				term.print_msg("Info reported from device:")
				term.print_labelled("  ModelNumber", self.model_number or "missing?")
				term.print_labelled("  DeviceClass", self.device_class or "missing?")
				term.print_labelled("  ProductType", self.product_type)
				term.print_labelled("  ProductName", product_name or "unknown")
				# if we are here, chances are storage info may have been pulled to guesstimate disk size
				if (storage := self.get_storage_info()):
					term.print_labelled("TotalDiskCapacity", f"~ {round(storage["TotalDiskCapacity"] / 1024 / 1024 / 1024, 2)} GB")
				print()

				term.print_warning("* Press CTRL+C to cancel")
				self._info["BaseModelNumber"] = self.model_number or term.input("Model number (5-char):", allow_ctrlc=True)
				self._info["ModelName"] = term.input("Model name:", product_name, allow_ctrlc=True)
				self._info["ModelStorage"] = term.input("Storage size: ", allow_ctrlc=True)
				self._info["ModelColor"] = term.input("Device color: ", allow_ctrlc=True)
				self._info["ModelReleaseDate"] = term.input("Release date: ", allow_ctrlc=True)

				return DeviceID.store_model_record(self._info)
			else:
				match_key = list(narrowed_matches.keys())[0]
				match = narrowed_matches[match_key]

				self._info["BaseModelNumber"] = match_key
				self._info["ModelName"] = match[1]
				self._info["ModelStorage"] = match[2]
				self._info["ModelColor"] = match[3]
				self._info["ModelReleaseDate"] = match[4] or None

				if not self.model_number:
					logger.warning("assuming model number like %s" % match_key)
					self._info["ModelNumber"] = match_key

				logger.info("* device with ECID %s is (assumed to be) a(n) %s" % (self.ecid, match[1]))
				return True
		else:
			for s in [
				"** unable to determine exact device model:",
				"  ECID       : %s" % self.ecid,
				"  ProductType: %s" % self.product_type
			]:
				logger.warning(s)
			return False

	@classmethod
	def _get_idinfo_from_udid(cls, udid):
		# ideally, try to do all polling for info upfront when the device is detected
		rtn, output = _libimd("ideviceinfo", "--udid", udid, "--xml", timeout=10)
		# note: THIS MAY FAIL if MDM policy prevents device connection to computers (ex. FM Services iPads in Normal Boot mode)
		# "ERROR: Could not connect to lockdownd: MC protected (-38)"
		# TODO: test output to see if there are any errors
		if not output:
			logger.error(f"unable to retrieve info from device with UDID {udid} (ideviceinfo: error {rtn})")
			term.print_error(f"unable to retrieve info from device with UDID {udid} (ideviceinfo: error {rtn})")
			return None

		if "ERROR: " in output:
			if "(-38)" in output:
				# MC protected
				# 
				# no information can be retrieved or actions sent; this device is prevented by MDM policy from communicating with computers over USB
				# 
				logger.error("unable to retrieve info from device with UDID %s -- MDM on device is blocking communication over USB" % udid)
				term.print_error("The MDM enrollment on device with UDID %s is blocking communication over USB" % udid)
				term.print_warning("* This device will need put in recovery mode manually to interact with it.")
				return None
			# elif ...
			else:
				# ??
				# TODO: check if a recovery job has been started on this device (add a flag for this)
				logger.error("unable to retrieve info from device with UDID %s" % udid)
				logger.error(f"ideviceinfo error {rtn}: {output}")
				term.print_error("unable to retrieve info from device with UDID %s - make sure it is unlocked or in recovery mode" % udid)
				term.print_error(f"  (error {rtn}: {output})")
				return None

		return decode_plist(ElementTree.XML(output))

	@classmethod
	def from_udid(cls, udid) -> Device | None:
		plist = cls._get_idinfo_from_udid(udid)
		if not plist:
			logger.warning(f"* unable to retrieve info from device with UDID {udid}")
			return None

		plist["BootMode"] = "Normal"

		dev = Device(plist)
		dev._storage = dev.get_storage_info()
		dev._gasgauge = dev.get_power_info()
		dev._battery = dev.get_battery_info()
		dev._mdm = dev.get_mdm_info()
		dev._locale = dev.get_locale_info()
		# poll for second line on compatible devices
		_ = dev.phone_number_2

		# more robust device model inference
		dev.infer_model()

		# term.print(f"")
		return dev

	@classmethod
	def from_ecid(cls, ecid) -> Device:
		rtn, output = _libimd("irecovery", "--ecid", f"0x{ecid.upper()}", "--query")
		if not output:
			logger.warning(f"* unable to retrieve info from device with ECID {ecid}")
			return None

		output = output.split("\n")
		info = {prop.split(": ")[0]: prop.split(": ")[1] for prop in output}

		# print(info)

		# "ProductType": info["PRODUCT"] => "Model Name"
		device_info = {
			"SerialNumber": sn if (Attribute.Recovery.SERIAL_NUMBER in info) and (sn := info[Attribute.Recovery.SERIAL_NUMBER]) != "N/A" else None,
			"DieID": int(info[Attribute.Recovery.ECID], 16),
			"ChipID": int(info[Attribute.Recovery.CHIP_ID], 16),
			"ProductType": (product_type := info[Attribute.Recovery.PRODUCT_TYPE]),
			"ModelName": IMobileDevice.ipsw.device_names[product_type],
			"BootMode": info[Attribute.Recovery.MODE] # Recovery, or DFU
		}

		return Device(device_info, in_recovery=True)

	# dumps all collected device info to json file/console
	def dump_info(self, *, to_file=True, to_stdout=False, filename=None):
		if to_file:
			if not filename:
				filename = f"{IMobileDevice.LOG_PATH}/device-{self.serial_number}.json"
			with open(filename, "w") as f:
				f.write(json.dumps(self._info, indent=4))

		if to_stdout:
			print(json.dumps(self._info, indent=4))

	# Mapping methods
	def __getitem__(self, key):
		if key not in self._info:
			return None
		return self._info[key]

	def __contains__(self, key):
		return key in self._info

	def __iter__(self):
		return iter(self._info)

	def __len__(self):
		return len(self._info)

	def __eq__(self, other):
		if self is other:
			return True

		if isinstance(other, type(self)):
			return self._id == other._id

		return False

	# Special overrides
	def __repr__(self):
		repr_keys = [
			"SerialNumber", "DeviceName", "BuildVersion",
			"ProductVersion", "PhoneNumber", "ProductType",
			"ModelNumber", "RegionInfo", "ModelName",
			"ModelStorage", "ModelColor", "ModelReleaseDate",
			"BootMode"
		]
		dict_repr = {key: self[key] for key in self._info.keys() if key in repr_keys and self[key] is not None}

		return f"{self.__class__.__name__}({dict_repr})"

	def __str__(self):
		return str(self._info)

	# functions
	def ping(self, *, refresh=False) -> bool:
		# checks to see if the device is still connected/accessible
		logger.debug("pinging device with id %s" % self.identifier)

		was_booted_normally = (self.bootmode == "normal")

		# much more efficient way:
		in_normal = False
		in_recovery = False
		udid = None
		# if the UDID is present, search using that to check if it's in normal mode
		# ECID should always be present
		# Serial will be present if iOS is not mangled
		if self.udid:
			in_normal = "not found" not in _libimd("ideviceinfo", "--udid", self.udid, "--xml")[1]
			udid = self.udid
		else:
			# if it's in recovery mode, this is good
			# do a rescan and get all connected UDIDs/ECIDs
			in_recovery = "Unable to connect to device" not in _libimd("irecovery", "--ecid", self.ecid, "--query")[1]
			
			if not in_recovery:
				# else, get all normal devices and see if any other info matches (Serial, ECID)
				ndevs = IMobileDevice.get_connected_devices()
				# do we have a serial?
				if self.serial_number:
					for dev in ndevs:
						if dev.serial_number == self.serial_number:
							udid = dev.udid
							in_normal = True
				else:
					for dev in ndevs:
						if dev.ecid == self.ecid:
							udid = dev.udid
							in_normal = True

		if not in_normal and not in_recovery:
			in_recovery = "Unable to connect to device" not in _libimd("irecovery", "--ecid", self.ecid, "--query")[1]

		if in_normal:
			# self._id = identifier
			self._in_recovery = False
			self._connected = True
			logger.debug(f"* found device with id {self.identifier}")
			self._info["BootMode"] = "normal"

			# refresh any available stats, as the boot-mode may have changed
			
			if refresh or not was_booted_normally:
				plist = Device._get_idinfo_from_udid(udid)
				if not plist:
					return False
						
				plist["BootMode"] = "Normal"
				self._info = plist

				self._storage = self.get_storage_info(refresh=True)
				self._gasgauge = self.get_power_info(refresh=True)
				self._battery = self.get_battery_info(refresh=True)
				self._mdm = self.get_mdm_info(refresh=True)
				self._locale = self.get_locale_info(refresh=True)
				_ = self.phone_number_2
				self.infer_model()

			return True
		elif in_recovery:
			self._in_recovery = True
			self._connected = True
			logger.debug(f"* found recovery device with id {self.identifier}")
			self._info["BootMode"] = "recovery"

			return True
		else:
			self._connected = False
			return False

	def shutdown(self):
		term.print_warning(f"[{self.serial_number or self.ecid}] sending shutdown command to device")
		rtn, output = _libimd("idevicediagnostics", "--udid", self.udid, "shutdown")

	def restart(self):
		term.print_warning(f"[{self.serial_number or self.ecid}] sending restart command to device")
		rtn, output = _libimd("idevicediagnostics", "--udid", self.udid, "restart")

	def prevent_erase(self):
		self._restore_protection = True
	def allow_erase(self):
		self._restore_protection = False

	def enter_recovery(self, *, wait=False, max_wait_secs=60):
		term.print_warning(f"[{self.serial_number or self.ecid}] sending enter recovery command to device")
		# logger.info("sending enter recovery command to device %s" % self.ecid)
		rtn, output = _libimd("ideviceenterrecovery", self.udid)

		if wait:
			sleep(1)
			logger.info("awaiting device reconnection...")
			starttime = int(time())
			while int(time()) - starttime < max_wait_secs:
				if self.ping():
					if self.bootmode == "normal":
						# not in recovery, may still be shutting down?
						sleep(3)
					elif self.bootmode == "recovery":
						# got it!
						logger.info("%s successfully entered recovery mode" % self.ecid)
						return True
				else:
					# device not detected in either mode
					sleep(3)

			# if not returned by now with a successful ping
			logger.warning("sent command, but device not responding to ping; check connection and try again")
			return False
		else:
			# command was sent and no wait is requested
			# logger.info("command sent")
			return True

	def exit_recovery(self, *, wait=False, max_wait_secs=60):
		term.print_warning(f"[{self.serial_number or self.ecid}] sending exit recovery command to device")
		# logger.info("sending exit recovery command to device %s" % self.ecid)
		rtn, output = _libimd("irecovery", "--ecid", self.ecid, "--normal")

		if wait:
			sleep(1)
			logger.info("awaiting device reconnection...")
			starttime = int(time())
			while int(time()) - starttime < max_wait_secs:
				if self.ping():
					if self.bootmode == "normal":
						# got it!
						logger.info("%s successfully entered normal boot mode" % self.ecid)
						return True
					elif self.bootmode == "recovery":
						# still in recovery, may still be thinking?
						sleep(3)
				else:
					# device not detected in either mode
					sleep(3)

			# if not returned by now with a successful ping
			logger.warning("sent command, but device not responding to ping; check connection and try again")
			return False
		else:
			# command was sent and no wait is requested
			# logger.info("command sent")
			return True

	def update(self):
		# idevicerestore --ecid [ECID] --no-input --plain-progress
		logfile = normalize_path(IMobileDevice.LOG_PATH, f"update-{self.serial_number}-{strftime("%H.%M.%S")}.log")
		ipsw = self.get_restore_ipsw_filename() or IMobileDevice.get_ipsw_path()

		rtn, _ = _libimd("idevicerestore", "--ecid", self.ecid, "--no-input", "--restore-mode", f"--logfile={logfile}", "--cache-path", IMobileDevice.get_ipsw_path(), timeout=9001, restore_job=True)

	def restore(self, *, logfile=None, suppress_msgs=False) -> int | None:
		# can always use device ECID to target for restore
		# idevicerestore --ecid [ECID] --restore-mode --erase --no-input --plain-progress [PATH to ipsws]
		# 
		if self._restore_protection:
			term.print_error("** Device %s (%s) is currently being protected from restores" % (self.model_name, self.ecid))
			return None

		self._is_recovering = True
		starttime = time()
		if not suppress_msgs:
			term.print_warning(f"[{self.serial_number or self.ecid}] beginning restore process")

		if not logfile:
			logfile = normalize_path(IMobileDevice.LOG_PATH, f"restore-{self.serial_number}-{strftime("%H.%M.%S")}.log")

		ipsw = self.get_restore_ipsw_filename()["fullpath"] or IMobileDevice.get_ipsw_path()

		rtn, _ = _libimd("idevicerestore", "--ecid", self.ecid, "--no-input", "--restore-mode", "--erase", f"--logfile={logfile}", ipsw, timeout=9001, restore_job=True)

		self._is_recovering = False
		endtime = time()
		logger.debug("restore process completed in %d minutes" % (round(int(endtime - starttime) / 60, 2)))

		if rtn == 0:
			term.print_success(f"[{self.serial_number or self.ecid}] restore completed in {round(int(endtime - starttime) / 60, 2)} minutes")
		else:
			term.print_warning(f"[{self.serial_number or self.ecid}] restore FAILED in {round(int(endtime - starttime) / 60, 2)} minutes")
			term.print_warning(f"* please check the logfile for this device: {logfile}")

		return rtn

	# fetching info
	# 
	# 
	def get_domain_info(self, domain, refresh=False, timeout=5):
		if domain not in self._domains or refresh:
			rtn, output = _libimd("ideviceinfo", "--udid", self.udid, "--domain", domain, "--xml", timeout=timeout)
			if output is None:
				# self._domains[domain] = {"error": "unable to retrieve domain '%s' from UDID %s" % (domain, self.udid)}
				logger.warning("unable to retrieve domain '%s' from UDID %s" % (domain, self.udid))
				self._domains[domain] = None
			else:
				self._domains[domain] = decode_plist(ElementTree.XML(output))
		return self._domains[domain]

	def get_power_info(self, refresh=False):
		if self._gasgauge is None or refresh:
			rtn, output = _libimd("idevicediagnostics", "--udid", self.udid, "diagnostics", "GasGauge", timeout=5)
			if output is None:
				logger.warning("unable to retrieve GasGauge from UDID %s" % self.udid)
				self._gasgauge = None
			else:
				self._gasgauge = decode_plist(ElementTree.XML(output))["GasGauge"]
		return self._gasgauge

	"""
		with com.apple.disk_usage.factory:
		# (values from uwuPhone)
		# AmountDataAvailable
		# AmountDataReserved
		# AmountRestoreAvailable
		# CalendarUsage
		# CameraUsage: 803183087
		# MediaCacheUsage
		# PhotoUsage: 803183087
		# TotalDataAvailable
		# TotalDataCapacity
		# TotalDiskCapacity
		# TotalSystemAvailable: 0
		# TotalSystemCapacity
		# VoicemailUsage
		# WebAppCacheUsage
	"""
	def get_storage_info(self, refresh=False):
		output = self.get_domain_info(Attribute.Domain.FACTORY_DISK_USAGE, refresh)
		if not output:
			output = self.get_domain_info(Attribute.Domain.DISK_USAGE, refresh)
			if not output:
				return {}
				
		if Attribute.Domain.FACTORY_DISK_USAGE in self._domains:
			return {key: output[key] for key in output.keys() if key in ["AmountDataAvailable", "AmountDataReserved", "CalendarUsage", "CameraUsage", "MediaCacheUsage", "PhotoUsage", "VoicemailUsage", "WebAppCacheUsage", "TotalDataAvailable", "TotalDataCapacity", "TotalDiskCapacity", "TotalSystemAvailable", "TotalSystemCapacity"]}
		else:
			return {key: output[key] for key in output.keys() if key in ["TotalDataAvailable", "TotalDataCapacity", "TotalDiskCapacity", "TotalSystemAvailable", "TotalSystemCapacity"]}

	def get_battery_info(self, refresh=False):
		return self.get_domain_info(Attribute.Domain.BATTERY, refresh)

	def get_mdm_info(self, refresh=False):
		return self.get_domain_info(Attribute.Domain.CHAPERONE, refresh)

	def get_locale_info(self, refresh=False):
		output = self.get_domain_info(Attribute.Domain.INTERNATIONALIZATION, refresh)
		if not output:
			return {}

		return {key: output[key] for key in output.keys() if key in ["Language", "Locale"]}

	def get_installed_apps(self, refresh=False):
		if self._user_apps and not refresh:
			return self._user_apps

		# XML output reveals MUCH more data
		rtn, output = _libimd("ideviceinstaller", "--udid", self.udid, "list", "--user")
		if not output:
			return []

		self._user_apps = []
		for line in output.split("\n"):
			fields = [f.strip().replace("\"", "") for f in line.split(",")]
			# ['CFBundleIdentifier', 'CFBundleShortVersionString', 'CFBundleDisplayName']
			if fields[0] == "CFBundleIdentifier":
				continue
			self._user_apps.append(MobileApp(CFBundleIdentifier=fields[0], CFBundleShortVersionString=fields[1], CFBundleDisplayName=fields[2]))

		return self._user_apps

	def get_system_apps(self, refresh=False):
		if self._system_apps and not refresh:
			return self._system_apps

		# XML output reveals MUCH more data
		rtn, output = _libimd("ideviceinstaller", "--udid", self.udid, "list", "--system")
		if not output:
			return []

		self._system_apps = []
		for line in output.split("\n"):
			fields = [f.strip().replace("\"", "") for f in line.split(",")]
			# ['CFBundleIdentifier', 'CFBundleShortVersionString', 'CFBundleDisplayName']
			if fields[0] == "CFBundleIdentifier":
				continue
			self._system_apps.append(MobileApp(CFBundleIdentifier=fields[0], CFBundleShortVersionString=fields[1], CFBundleDisplayName=fields[2]))

		return self._system_apps

	def set_name(self, newname):
		rtn, _ = _libimd("idevicename", "--udid", self.udid, newname)
		# verify
		success = (_libimd("idevicename", "--udid", self.udid)[1].strip() == newname)
		if success:
			self._info["DeviceName"] = newname
		return success

	# convenience properties
	# 
	# device ID properties
	@property
	def identifier(self):
		return self._id

	@property
	def serial_number(self):
		return self[Attribute.SERIAL_NUMBER]

	@property
	def ecid(self):
		return "0x" + hex(self[Attribute.DIE_ID]).split("x")[-1].upper().rjust(16, "0")

	@property
	def chipid(self) -> str:
		return hex(self[Attribute.CHIP_ID])	

	@property
	def chip(self) -> ChipID:
		return ChipID.lookup(self.chipid)
	
	@property
	def udid(self):
		return self[Attribute.UDID]

	# iDeviceX,Y
	@property
	def product_type(self):
		return self["ProductType"]
	
	# iPhone, iPad, etc
	@property
	def device_class(self):
		return self["DeviceClass"]

	# MKVF2
	@property
	def model_number(self):
		return self["ModelNumber"]
	
	# LL/A
	@property
	def region_info(self):
		return self["RegionInfo"]

	# MKVF2LL/A
	@property
	def sku(self):
		return f"{self.model_number}{self.region_info}"
	
	@property
	def model_type(self):
		return {
			"F": "refurbished",
			"G": "refurbished",
			"M": "retail",
			"N": "replacement",
			"P": "personalized/engraved",
			"3": "in-store demo unit",
			"4": "'as-is' no warranty unit",
			"5": "warranty-less (no Apple)"
		}[self.model_number[0]]

	@property
	def model_name(self):
		return self["ModelName"]

	@property
	def model_storage(self):
		return self["ModelStorage"]

	@property
	def model_color(self):
		return self["ModelColor"]

	@property
	def model_releasedate(self):
		return self["ModelReleaseDate"]

	@property
	def full_model_name(self):
		_st = self.model_name
		if self.model_storage:
			_st += " " + self.model_storage
		if self.model_color:
			_st += " " + self.model_color
		return _st
	
	@property
	def name(self):
		return self["DeviceName"] or "Unknown"
	
	@property
	def is_iphone(self):
		return "iPhone" in self["ModelName"]

	@property
	def is_ipad(self):
		return "iPad" in self["ModelName"]

	@property
	def is_mac(self):
		return "Mac" in self["ModelName"]

	@property
	def osname(self):
		if self.is_iphone:
			return "iOS"
		elif self.is_ipad:
			return "iPadOS"
		elif self.is_mac:
			return "macOS"
		else:
			return "Other Apple OS"

	@property
	def osversion(self):
		return self["ProductVersion"]

	@property
	def osbuild(self):
		return self["BuildVersion"]

	@property
	def is_activated(self):
		return self["ActivationState"]

	@property
	def storage_capacity(self):
		return str(self.get_storage_info()["TotalDiskCapacity"]).replace("0", "") + "GB"

	@property
	def is_restoring(self) -> bool:
		return self._is_recovering
	
	
	# cellular-related properties
	@property
	def is_cellular_capable(self):
		return self["TelephonyCapability"] or False
	
	@property
	def has_sim1(self):
		return self.is_cellular_capable and self["SIMGID1"] is not None

	@property
	def has_sim2(self):
		return self.is_cellular_capable and self["SIM2GID1"] is not None

	@property
	def has_psim_slot(self) -> bool:
		if "SIM1IsEmbedded" in self._info:
			return not self["SIM1IsEmbedded"]

		if "SIMStatus" in self._info:
			return self["SIMStatus"] == "kCTSIMSupportSIMStatusReady" and self["SIMTrayStatus"] != "kCTSIMSupportSIMTrayAbsent"

		return self["SIMTrayStatus"] != "kCTSIMSupportSIMTrayAbsent"
	
	# kCTSIMSupportSIMTrayInsertedNoSIM 
	@property
	def has_psim_inserted(self):
		return self["SIMTrayStatus"] == "kCTSIMSupportSIMTrayInsertedWithSIM"

	@property
	def imei(self):
		return self.imei_1 or self.imei_2

	@property
	def imeis(self):
		if self.imei_1 and self.imei_2:
			return (self.imei_1, self.imei_2)
		else:
			return tuple([self.imei])
	
	@property
	def imei_1(self):
		return self["InternationalMobileEquipmentIdentity"] if self.is_cellular_capable else None

	@property
	def imei_2(self):
		return self["InternationalMobileEquipmentIdentity2"] if self.is_cellular_capable else None

	@property
	def iccid(self):
		return self.iccid_1 or self.iccid_2

	@property
	def iccids(self):
		if self.iccid_1 and self.iccid_2:
			return (self.iccid_1, self.iccid_2)
		else:
			return tuple([self.iccid])

	@property
	def iccid_1(self):
		return self["IntegratedCircuitCardIdentity"]

	@property
	def iccid_2(self):
		return self["IntegratedCircuitCardIdentity2"]
	
	@property
	def phone_number(self):
		return self.phone_number_1 or self.phone_number_2
	
	@property
	def phone_numbers(self):
		if self.phone_number_1 and self.phone_number_2:
			return (self.phone_number_1, self.phone_number_2)
		else:
			return tuple([self.phone_number])

	@property
	def phone_number_1(self):
		return self["PhoneNumber"]

	@property
	def phone_number_2(self):
		if "PhoneNumber2" not in self._info and "SIM2IsEmbedded" in self._info:
			self._info["PhoneNumber2"] = _libimd("ideviceinfo", "--udid", self.udid, "--key", "PhoneNumber2")[1]
		return self["PhoneNumber2"]

	@property
	def imsi(self):
		return self.imsi_1 or self.imsi_2
	
	@property
	def imsis(self):
		if self.imsi_1 and self.imsi_2:
			return (self.imsi_1, self.imsi_2)
		else:
			return tuple([self.imsi])

	@property
	def imsi_1(self):
		return self["InternationalMobileSubscriberIdentity"]

	@property
	def imsi_2(self):
		return self["InternationalMobileSubscriberIdentity2"]	

	@property
	def carrier(self) -> Carrier | None:
		return self.carrier_1 or self.carrier_2

	@property
	def carriers(self):
		if self.carrier_1 and self.carrier_2:
			return (self.carrier_1, self.carrier_2)
		else:
			return tuple([self.carrier])

	@property
	def carrier_1(self) -> Carrier | None:
		return Carrier.from_bundle(carrier_bundles[0]["CFBundleIdentifier"]) if (carrier_bundles := self.is_cellular_capable and self["CarrierBundleInfoArray"]) else (imsi1 := self.imsi_1) and Carrier.from_imsi(imsi1) or None

	@property
	def carrier_2(self) -> Carrier | None:
		return Carrier.from_bundle(carrier_bundles[1]["CFBundleIdentifier"]) if (carrier_bundles := self.is_cellular_capable and self["CarrierBundleInfoArray"]) and len(carrier_bundles) > 1 else (imsi2 := self.imsi_2) and Carrier.from_imsi(imsi2) or None
	
	@property
	def carrier_id(self):
		return self.carrier_id_1 or self.carrier_id_2

	@property
	def carrier_ids(self):
		if self.carrier_id_1 and self.carrier_id_2:
			return (self.carrier_id_1, self.carrier_id_2)
		else:
			return tuple([self.carrier_id])

	@property
	def carrier_id_1(self):
		if "CarrierBundleInfoArray" in self._info and len(self._info["CarrierBundleInfoArray"]) > 0:
			return self._info["CarrierBundleInfoArray"][0]["CFBundleIdentifier"]
		return None

	@property
	def carrier_id_2(self):
		if "CarrierBundleInfoArray" in self._info and len(self._info["CarrierBundleInfoArray"]) > 1:
			return self._info["CarrierBundleInfoArray"][1]["CFBundleIdentifier"]
		return None
	
	# other properties
	@property
	def bootmode(self):
		return "normal" if not self._in_recovery else "recovery"

	@property
	def icloud_account(self):
		# TODO: subkeys may not exist!!
		if "fm-account-masked" not in self["NonVolatileRAM"]:
			return "(n/a)"
		return self["NonVolatileRAM"]["fm-account-masked"]

	@property
	def icloud_locked(self):
		if "fm-activation-locked" in self["NonVolatileRAM"]:
			return self["NonVolatileRAM"]["fm-activation-locked"].upper() == "YES"
		return None

	# other functions to return data
	def get_line_number(self, num=1):
		if num == 1:
			if self.phone_number_1:
				if self.phone_number_1[:2] == "+1":
					return (line := "".join(filter(str.isdigit, self.phone_number_1))[-10:])[:3] + "-" + line[3:6] + "-" + line[6:]
				else:
					return "+" + "".join(filter(str.isdigit, self.phone_number_1))
			else:
				return None
		elif num == 2:
			if self.phone_number_2:
				if self.phone_number_2[:2] == "+1":
					return (line := "".join(filter(str.isdigit, self.phone_number_2))[-10:])[:3] + "-" + line[3:6] + "-" + line[6:]
				else:
					return "+" + "".join(filter(str.isdigit, self.phone_number_2))
			else:
				return None

	def get_storage_summary(self):
		if Attribute.Domain.DISK_USAGE in self._domains or Attribute.Domain.FACTORY_DISK_USAGE in self._domains:
			info = self.get_storage_info()

			# ["TotalDataAvailable", "TotalDataCapacity", "TotalDiskCapacity", "TotalSystemAvailable", "TotalSystemCapacity"]}
			avail = round(info[DomainKey.FactoryDiskUsage.DATA_AVAILABLE] / 1000000000, 1)
			if avail < 1.0:
				avail = f"{round(info[DomainKey.FactoryDiskUsage.DATA_AVAILABLE] / 1000000, 1)} MB"
			else:
				avail = f"{avail} GB"
			resp = {
				"Capacity": f"{round(info[DomainKey.FactoryDiskUsage.TOTAL_DISK_SIZE] / 1000000000, 1)} GB",
				"Available": avail
			}

			if Attribute.Domain.FACTORY_DISK_USAGE in self._domains:
				resp["Photos"] = f"{round(info[DomainKey.FactoryDiskUsage.PHOTOS] / 1000000, 1)} MB"
				resp["Calendar"] = f"{round(info[DomainKey.FactoryDiskUsage.CALENDAR] / 1000000, 1)} MB"
				resp["Voicemail"] = f"{round(info[DomainKey.FactoryDiskUsage.VOICEMAIL] / 1000000, 1)} MB"
				resp["System"] = f"{round(info[DomainKey.FactoryDiskUsage.SYSTEM_SIZE] / 1000000, 1)} MB"

			return resp
			
		
		return None 

	# Hao
	@cached_property
	def linq_im_code(self, /, *,
		normalizer: re.Pattern = re.compile("\\(|\\)|\"|\\-|inch|th|generation|gen|gb", re.IGNORECASE),
		abbreviator: Callable[[str, int], str] = cache(lambda token, length: token[0] if token.lower() not in {"plus", "air"} and len(token) >= length and not any(map(str.isdigit, token)) else token),
		soc_mapping: Mapping[str, str] = {"A16": "11", "T8103": "M1", "T8112": "M2", "T8122": "M3", "T8132": "M4", "T8142": "M5"}
	) -> str | None:		
		if (product_name := self.full_model_name) and (tokens := [soc_mapping.get(token, token) for token in normalizer.sub("", product_name).split()[1:]]):
			if self.is_iphone:
				# iPhone 6s Plus 128GB Space Gray -> iP6sPlus128SG
				# iPhone 15 Plus 128GB Black -> iP15Plus128B (Plus is unabbreviated to differentiate from Pro)
				# iPhone 15 Pro 128GB Black Titanium -> iP15Pro128BT (Pro is unabbreviated to differentiate from Plus)
				# iPhone 16 Pro Max 256GB Black Titanium -> iP16ProMax256BT
				# iPhone 17e 256GB Black -> iP17e256B
				return "iP" + "".join(abbreviator(token, 4) for token in tokens)
			if self.is_ipad:
				# iPad (A16) 128GB Silver -> iPad11128W+CS (WiFi and cellular capabilities and generation mapped from SoC)
				# iPad Pro (11-inch) (4th generation) 256GB Space Gray -> iPadPro11M2256W+CSG (display size, WiFi and cellular capabilities, and SoC mapped from generation)
				# iPad Pro 11-inch (M5) 256GB Space Black -> iPadPro11M5256W+CSB (display size, WiFi and cellular capabilities, and SoC)
				# iPad Pro 13-inch (M5) 1TB Silver -> iPadPro13M51TBW+CS (display size, WiFi and cellular capabilities, and SoC)
				storage_capacity_index: int = next(len(tokens) - index - 1 for index, value in enumerate(reversed(tokens)) if value.replace(".", "", 1).isdigit())
				soc_index: int = (tokens.index("") if "" in tokens else storage_capacity_index) - 1
				if (soc := soc_mapping.get(self[Attribute.HARDWARE_MODEL].upper(), soc_mapping.get(self[Attribute.HARDWARE_PLATFORM].upper(), soc_mapping.get(tokens[soc_index])))): tokens[soc_index] = soc
				
				computed_item_code = "iPad" + "".join(abbreviator(token, 4) for token in (*tokens[:storage_capacity_index + 1], *("Wi-Fi" + (" + Cellular" if self.is_cellular_capable else "")).split(), *tokens[storage_capacity_index + 1:]))
				# overrides for weird naming conventions that break normal rules
				if computed_item_code == "iPad1064W+CS":
					# iPad (10th Gen) 64GB Wi-Fi + Cellular Silver
					computed_item_code = "iPad1064WiFi+CS"

				return computed_item_code
	
	# returns filenames of ALL local firmwares found for a device, regardless of if they are signed or not
	def detect_all_firmwares(self, *, only_signed=False):
		# ipsw: str ::=> Firmware dict {}
		all_local_firmwares = IMobileDevice.ipsw.get_downloaded_firmwares_dict()["by_file"]
		all_dev_firmwares = IMobileDevice.ipsw.get_all_firmwares(self.product_type)

		filtered_local = {f: all_local_firmwares[f] for f in all_local_firmwares if self.product_type in all_local_firmwares[f]["device_ids"]}

		if only_signed:
			signed_versions = [f["version"] for f in all_dev_firmwares if f["signed"]]
			return {f: filtered_local[f] for f in filtered_local if filtered_local[f]["version"] in signed_versions}
		else:
			return filtered_local

	def detect_signed_firmwares(self):
		return self.detect_all_firmwares(only_signed=True)

	# returns filename of the newest firmware in the IPSW path that is currently signed
	def get_restore_ipsw_filename(self):
		signed = self.detect_signed_firmwares()
		if len(signed) == 0:
			return None

		versions = {signed[f]["version"]: f for f in signed}

		highest = "0"
		for v in versions:
			if IPSW.compare_versions(highest, v) > 0:
				highest = v

		if highest == "0":
			return None

		return signed[versions[highest]]

	# download a firmware for this device
	def download_firmware(self, *, version="latest"):
		pass

	# returns True if an app with the given bundle ID or Display Name (fuzzy matching) is installed on the device
	def is_app_installed(self, *, bundle=None, displayname=None):
		if bundle:
			matches = [app for app in self.get_installed_apps() if app.id.lower() == bundle.lower()]
			if len(matches) == 0:
				return False

			return True

		if displayname:
			term.print_warning("is_app_installed(displayname) not implemented")
			return False

		raise ValueError("One of bundle or displayname keyword args must be specified")

# end class Device
	
# INTERFACE class - contains app-related logic and functions, etc
# that interface with libimobiledevice and parse output
class IMobileDevice:
	PLATFORM: str
	PROGRAM_PATH: str = normalize_path(os.path.expanduser("~"), "idevice")
	# PROGRAM_PATH: str = os.getcwd()
	# LOG_PATH: str = normalize_path(PROGRAM_PATH, "logs", date.today())
	LOG_PATH: str = normalize_path(os.getcwd(), "logs", date.today())
	LIBRARY_PATH: str = normalize_path(PROGRAM_PATH, "libs")

	# MODELS_DICT_URL: str = "https://gist.githubusercontent.com/haozhang96/3b1ce6453099ef545d24d884d8e31fa5/raw/idevice_models.json"
	MODELS_DICT_URL: str = "https://gist.githubusercontent.com/jboby93/d00b1f171b733bf90a00fc9ede126777/raw/idevice_models.json"
	MODELS_DICT_FILE: str = "idevice_models.json"
	CHIPID_DICT_URL: str = "https://gist.githubusercontent.com/jboby93/365c8a6f2905f76fbe38f2e38baf20dc/raw/chipid-dict.json"
	CHIPID_DICT_FILE: str = "chipid-dict.json"
	DEVICE_DICT_URL: str = "https://gist.githubusercontent.com/jboby93/7ea494caef7f05d1d1da42383c8c7954/raw/device_names.json"
	DEVICE_DICT_FILE: str = "device_names.json"

	LIBIMOBILEDEVICE_PATH: str
	LIBIMOBILEDEVICE_WINDOWS: str = "https://github.com/L1ghtmann/libimobiledevice/releases/download/suite-exe-21d57a9/libimobile-suite-latest_x86_64-mingw64.tar.xz:948d20c5f6460ab9d9ac6b5fdeba00ddde0c62f59882e1e1e577ed16b3ae8abe"
	LIBIMOBILEDEVICE_MACOS: str = "https://gist.githubusercontent.com/nikias/84c79469a1d0f16ff95250f0d51858c3/raw/limd-build-macos.sh:c985256f69bfe761690f1998fddb64df20a18fffd936217570ea71535a241c9a"

	LIBQRENCODE_PATH: str
	LIBQRENCODE_WINDOWS: str = "https://master.dl.sourceforge.net/project/qrencode-for-windows/QREncode-4.1.1_Win32(static).zip?viasf=1:eb3afc0f87bf9f1c9af143fcd205c29c07f605bf"
	LIBQRENCODE_EXE: str

	config = {}

	firmware_path = None
	models_dict = {}
	ipsw = None
	cached_devices = {}

	# Windows commands
	CMD_DEVMGR_FIND_UDIDS = ('powershell', '-NoProfile', '-Command', 'Get-PnpDevice -FriendlyName "Apple Mobile Device USB Composite Device" -PresentOnly | Select -ExpandProperty DeviceID')
	CMD_DEVMGR_FIND_ECIDS = ("powershell", "-NoProfile", "-Command", "Get-PnpDevice -FriendlyName 'Apple Recovery *' -PresentOnly | Select -ExpandProperty DeviceID")

	# macOS commands
	CMD_IOREG_FIND_ECIDS = ("ioreg", "-r", "-w0", "-n", "Apple Mobile Device (Recovery Mode)")

	@classmethod
	def get_device_from_id(cls, deviceid: DeviceID):
		# attempts to find if a device with the given ID is still connected
		# check normal-mode devices using UDID if present
		# (can also scan for devices using
		# 
		# Windows:
		# _libimd('powershell', '-NoProfile', '-Command', 'Get-PnpDevice -FriendlyName "Apple Mobile Device USB Composite Device" -PresentOnly | Select -ExpandProperty DeviceID')
		# 
		# macOS: (TODO)
		# _libimd("ioreg", "-r", "-w0", "-n", "Apple Mobile Device ???")
		# 
		# )
		# check recovery-mode devices using ECID if present (should always be present because it can be obtained through normal mode` )
		pass
		
	@classmethod
	def get_name_for_product_type(cls, product_type):
		if product_type in cls.ipsw.device_names:
			return cls.ipsw.device_names[product_type]
		return None

	@classmethod
	def get_last_modified(cls):
		return datetime.fromtimestamp(os.path.getmtime(sys.argv[0])).strftime("%Y-%m-%d %H:%M:%S")

	@classmethod
	def initialize(cls):
		# if not os.path.exists(cls.PROGRAM_PATH):
		# 	os.makedirs(cls.PROGRAM_PATH)

		if not os.path.exists(cls.LOG_PATH):
			os.makedirs(cls.LOG_PATH)

		if os.path.exists("config.json"):
			with open("config.json", "r") as cf:
				pass
		else:
			cls.config = {
				"ProgramPath": "",
				"LogPath": "",
				"Version": APP_VERSION
			}

		cls.prepare_lookup_dicts()
		cls.prepare_runtime()
		cls.ipsw = IPSW()
	# end initialize()			
	
	@classmethod
	def prepare_lookup_dicts(cls):
		if os.path.exists(cls.MODELS_DICT_FILE) or download_file(cls.MODELS_DICT_URL, cls.MODELS_DICT_FILE):
			with open(cls.MODELS_DICT_FILE, "r") as f:
				cls.models_dict = json.loads(f.read())
		else:
			term.print_error(f"** unable to collect {cls.MODELS_DICT_URL} - device identification will not fully work")
			term.pause()

		if os.path.exists(cls.CHIPID_DICT_FILE) or download_file(cls.CHIPID_DICT_URL, cls.CHIPID_DICT_FILE):
			with open(cls.CHIPID_DICT_FILE, "r") as f:
				cls.chipid_dict = json.loads(f.read())
		else:
			term.print_error(f"** unable to collect {cls.CHIPID_DICT_URL} - ChipID identification will not work")
			term.pause()

		if os.path.exists(cls.DEVICE_DICT_FILE) or download_file(cls.DEVICE_DICT_URL, cls.DEVICE_DICT_FILE):
			with open(cls.DEVICE_DICT_FILE, "r") as f:
				cls.devices_dict = json.loads(f.read())
		else:
			term.print_error(f"** unable to collect {cls.DEVICE_DICT_URL} - device identification will not work")
			term.pause()

	@classmethod
	def prepare_runtime(cls):
		term.print_warning("* Verifying dependencies...")

		cls.PLATFORM = platform.system()

		match cls.PLATFORM:
			case "Windows":
				win_install_to_script_dir = False
				cls.PROGRAM_PATH = normalize_path(os.path.expanduser("~"), "idevice")
				cls.LIBRARY_PATH = normalize_path(cls.PROGRAM_PATH, "libs")

				if os.path.exists(normalize_path(os.path.expanduser("~"), "idevice")):
					cls.PROGRAM_PATH = normalize_path(os.path.expanduser("~"), "idevice")
				elif os.path.exists(normalize_path(os.path.expanduser("~"), "AppData", "Local", "idevice")):
					cls.PROGRAM_PATH = normalize_path(os.path.expanduser("~"), "AppData", "Local", "idevice")
				elif os.path.exists(normalize_path(os.getcwd(), "idevice")):
					cls.PROGRAM_PATH = normalize_path(os.getcwd(), "idevice")
				else:
					cls.PROGRAM_PATH = term.modalalert("Important message", f"It looks like this is your first time running the script.  We need to install a few dependencies first.\n\nThis script uses libimobiledevice and libqrencode, both free and open-source software, for various actions.\n\nYou can install these to your user folder, AppData, or the current directory containing the script files.",
							buttons=[
								{
									"label": "User folder",
									"value": normalize_path(os.path.expanduser("~"), "idevice")
								},
								{
									"label": "AppData",
									"value": normalize_path(os.path.expanduser("~"), "AppData", "Local", "idevice")
								},
								{
									"label": "Current Directory",
									"value": normalize_path(os.getcwd(), "idevice")
								}
							], 
							default_button=0, 
							background_title=f"{APP_NAME} {APP_VERSION} - First-time setup",
							background_color="blue"
						)

				cls.LIBRARY_PATH = normalize_path(cls.PROGRAM_PATH, "libs")
				cls.LIBIMOBILEDEVICE_PATH = normalize_path(cls.LIBRARY_PATH, "libimobiledevice")
				# cls.LOG_PATH = normalize_path(cls.PROGRAM_PATH, "logs", date.today())

				if not os.path.isfile(normalize_path(cls.LIBIMOBILEDEVICE_PATH, "irecovery.exe")):
					term.print_msg("  Downloading libimobiledevice...")
					with open(path := urlretrieve(cls.LIBIMOBILEDEVICE_WINDOWS[:cls.LIBIMOBILEDEVICE_WINDOWS.rindex(":")])[0], "rb") as file:
						if file_digest(file, "sha256").hexdigest() == cls.LIBIMOBILEDEVICE_WINDOWS[cls.LIBIMOBILEDEVICE_WINDOWS.rindex(":") + 1:]:
							with tarfile.open(path) as archive:
								archive.extractall(cls.LIBIMOBILEDEVICE_PATH, filter=tarfile.fully_trusted_filter)

				cls.LIBQRENCODE_PATH = normalize_path(cls.LIBRARY_PATH, "libqrencode")
				if not os.path.exists(cls.LIBQRENCODE_PATH):
					os.makedirs(cls.LIBQRENCODE_PATH)

				if not os.path.isfile(normalize_path(cls.LIBQRENCODE_PATH, "qrencode.exe")):
					term.print_msg("  Downloading libqrencode...")
					with open(path := urlretrieve(cls.LIBQRENCODE_WINDOWS[:cls.LIBQRENCODE_WINDOWS.rindex(":")])[0], "rb") as file:
						if file_digest(file, "sha1").hexdigest() == cls.LIBQRENCODE_WINDOWS[cls.LIBQRENCODE_WINDOWS.rindex(":") + 1:]:
							with zipfile.ZipFile(path) as archive, archive.open(archive.namelist()[-1]) as source, open(normalize_path(cls.LIBQRENCODE_PATH, "qrencode.exe"), "wb") as target:
								copyfileobj(source, target)
				cls.LIBQRENCODE_EXE = "qrencode.exe"
			case "Darwin":
				if not os.path.exists(cls.PROGRAM_PATH):
					os.makedirs(cls.PROGRAM_PATH)

				if not os.path.exists(cls.LOG_PATH):
					os.makedirs(cls.LOG_PATH)

				use_which = (which := system("which")[1]) is not None and (which == "" or which.startswith("usage:"))
				use_which = True
				cls.LIBIMOBILEDEVICE_PATH = "/usr/local/bin/"
				cls.LIBQRENCODE_PATH = "/usr/local/bin/"
				cls.LIBQRENCODE_EXE = "qrencode"

				libmobiledevice_hash = cls.LIBIMOBILEDEVICE_MACOS.split(":")[-1]
				libmobiledevice_url = cls.LIBIMOBILEDEVICE_MACOS.replace(":" + libmobiledevice_hash, "")

				installsh = urlretrieve(libmobiledevice_url, os.path.join(cls.PROGRAM_PATH, "limd-build-macos.sh"))
				libqrencode_path = cast(str, system("which", "qrencode")[1])
				if str(system("which", "irecovery")[1]) in ["", "irecovery not found"]:
					installsh_ex = installsh[0]
					# with open(installsh, "rb") as file:
					# 	print(str(file_digest(file, "sha256").hexdigest()))
					# 	if str(file_digest(file, "sha256").hexdigest()).strip() == libmobiledevice_hash.strip():
					term.print_msg("  Downloading libimobiledevice...")
					system("bash", installsh_ex, interactive=True)

				if use_which:
					cls.LIBIMOBILEDEVICE_PATH = os.path.dirname(str(system("which", "irecovery")[1]))

				if system("which", "qrencode")[1] == "qrencode not found" if use_which else not os.path.isfile(normalize_path(cls.LIBQRENCODE_PATH, "qrencode")):
					term.print_msg("  Downloading libqrencode...")
					system("brew", "install", "libqrencode")
				if use_which:
					cls.LIBQRENCODE_PATH = "/".join(str(system("which", "qrencode")[1]).split("/")[:-1])
			case "Linux":
				if not os.path.exists(cls.PROGRAM_PATH):
					os.makedirs(cls.PROGRAM_PATH)

				if not os.path.exists(cls.LOG_PATH):
					os.makedirs(cls.LOG_PATH)

				cls.LIBIMOBILEDEVICE_PATH = "/usr/local/bin/"
				if not os.path.isfile(normalize_path(cls.LIBIMOBILEDEVICE_PATH, "irecovery")):
					term.print_msg("  Downloading libimobiledevice...")
					system("sudo", "apt-get", "install", "usbmuxd", "libimobiledevice6", "libimobiledevice-utils", interactive=True)

				cls.LIBQRENCODE_PATH = cls.LIBIMOBILEDEVICE_PATH # Same installation path
				cls.LIBQRENCODE_EXE = "qrencode"
				if not os.path.isfile(normalize_path(IMobileDevice.LIBQRENCODE_PATH, "qrencode")):
					term.print_msg("  Downloading libqrencode...")
					system("sudo", "apt-get", "install", "libqrencode", interactive=True)
			case _:
				raise RuntimeError("Unsupported platform: " + PLATFORM)

	@classmethod
	def get_ipsw_path(cls):
		return cls.ipsw.get_path()

	@classmethod
	def set_ipsw_path(cls, newpath=None):
		if not newpath:
			# show folder selection
			oldpath = cls.ipsw.get_path()
			newpath = term.filebrowser("Select firmware directory", "Choose the folder containing your .ipsw firmware files", start_dir=oldpath, allow_mkdir=True, folder_select=True, show_files_in_folder_select=True)
			if not newpath:
				return

		if not os.path.exists(newpath):
			# create if needed
			if term.input_yn(f"The path '{newpath}' doesn't exist -- create it?"):
				os.makedirs(newpath, exist_ok=True)
			else:
				return

		cls.ipsw.set_path(newpath)

	@classmethod
	def manage_ipsw(cls):
		IPSWApp.main(ipsw=cls.ipsw)

	# imobiledevice methods
	@classmethod
	def get_connected_ids(cls):
		term.print_msg("Searching for devices...")
		rtn, connected = _libimd("idevice_id", "--list")
		if not connected:
			return []
		if connected == "ERROR: Unable to retrieve device list!" and cls.PLATFORM == "Windows":
			term.print_error("** Unable to detect Apple devices - iTunes likely is not installed")
			term.print_warning("Please install iTunes before using this script.")
			term.print_labelled("  64-bit Windows", "https://www.apple.com/itunes/download/win64")
			term.print_labelled("  32-bit Windows", "https://www.apple.com/itunes/download/win32")
			print()

			return []

		return [this_id for _id in connected.split("\n") if (this_id := _id.strip()) != ""]

	@classmethod
	def get_connected_devices(cls):
		# get_connected_ids() will return UDIDs of devices that may already be in the middle of a restore!
		devices = []
		for udid in cls.get_connected_ids():
			if (d := Device.from_udid(udid)):
				devices.append(d)
			else:
				term.print_error("* unable to connect to device with UDID %s" % udid)

		return devices

		# term.print_msg("Querying device information...")
		#return [Device.from_udid(udid) for udid in cls.get_connected_ids()]


	# Note: devices show up in Device Manager
	# > USB devices > Apple Recovery (iBoot) USB Composite Device > Details > Device instance path
	# contains ECID and SN
	# ex) USB\VID_05AC&PID_1281\SDOM:01_CPID:8003_CPRV:01_CPFM:03_SCEP:01_BDID:06_ECID:001A519804FA4F26_IBFL:1D_SRNM:[FCCV11CGGRX8]
	# 
	# for devices with failed restores/DFU/broken OS,
	# ex) USB\VID_05AC&PID_1281\SDOM:01_CPID:8140_CPRV:10_CPFM:03_SCEP:01_BDID:04_ECID:0015042A2283801C_IBFL:3D_SIKA:00
	# 
	# powershell to find all devices in recovery mode:
	# ("powershell", "-NoProfile", "-Command", "Get-PnpDevice -FriendlyName \"Apple Recovery *\" -PresentOnly | Select -ExpandProperty DeviceID")
	# 
	# returns list of (ECID, SERIAL) pairs (SERIAL will be None for DFU/brokenOS devices)
	@classmethod
	def get_recovery_ids(cls):
		term.print_msg("Searching for recovery-mode devices...")
		match cls.PLATFORM:
			case "Windows":
				# Windows only (for now)
				rtn, output = _libimd(*cls.CMD_DEVMGR_FIND_ECIDS)
			case "Darwin":
				rtn, output = _libimd(*cls.CMD_IOREG_FIND_ECIDS)
			case "Linux":
				pass

		if not output:
			return []

		# first, go through the results and capture ALL ECIDs
		ecid_matcher = re.compile("ECID:([0-9A-F]+)", re.IGNORECASE)
		matcher = re.compile("ECID:([0-9A-F]+).+SRNM:\\[([0-9A-Z]+)\\]", re.IGNORECASE)
		# the ones that only match the ECID regex do not have an accessible SN yet => likely DFU or broken OS
		ecids = list(dict.fromkeys([ecid_matcher.findall(row)[0] for row in output.split("\n") if len(ecid_matcher.findall(row)) > 0]))
		# the ones that return both ECID and SN are in a normal/recovery boot mode
		fullpairs = list(dict.fromkeys([matcher.findall(row)[0] for row in output.split("\n") if len(matcher.findall(row)) > 0]))

		response = []
		for ecid in ecids:
			appended = False
			for pair in fullpairs:
				if pair[0] == ecid:
					# this result has a SN
					response.append(pair)
					appended = True
			if not appended:
				# no SN found for this one
				response.append((ecid, None))

		# removes duplicates
		# return list(dict.fromkeys([matcher.findall(row)[0] for row in output.split("\n") if len(matcher.findall(row)) > 0]))
		return response

	"""
		> irecovery --query -ecid [ecid]
		CPID: 0x8003 (stored as ChipID: Decimal in Normal mode)
		CPRV: 0x01
		BDID: 0x06
		ECID: 0x001a519804fa4f26 (stored as DieID: Decimal in Normal mode)
		CPFM: 0x03
		SCEP: 0x01
		IBFL: 0x1d
		SRTG: N/A
		SRNM: FCCV11CGGRX8
		IMEI: N/A
		NONC: 89e40bdc4e109179d769771805fd7893b94257d2
		SNON: bdcd4fd8a41ad0f0cee019436429f7a0931324b3
		MODE: Recovery
		PRODUCT: iPhone8,2
		MODEL: n66map
		NAME: iPhone 6s Plus
	"""
	@classmethod
	def get_recovery_devices(cls):
		# term.print_msg("Searching for recovery-mode devices...")
		return [Device.from_ecid(rid[0]) for rid in cls.get_recovery_ids()]

	@classmethod
	def get_info_for(cls, devid):
		if (output := _libimd("ideviceinfo", "--udid", devid, "--xml")) and output[1]:
			plist = ElementTree.XML(output[1])
			return decode_plist(plist)
		else:
			return {}

	# in: List of Devices
	# output: mapping of DeviceModel (iPhoneX,Y) => the correct firmware file to restore this device, or None if it needs to be downloaded
	# 	- the firmware must be currently signed by Apple to qualify
	@classmethod
	def detect_firmwares_for_devices(cls, devices):
		firmware_map = {}
		firmwares = cls.ipsw.get_downloaded_firmwares_dict()["by_file"]

		# check signed with is_firmware_signed(devid, version=... OR build=...)
		for dev in devices:
			matches = {ipsw: firmwares[ipsw] for ipsw in firmwares if (dev.product_type in firmwares[ipsw]["device_ids"]) and cls.ipsw.is_firmware_signed(dev.product_type, version=firmwares[ipsw]["version"])}
			firmware_map[dev.product_type] = matches

		# if there are multiple matches (signed firmwares for a product type), any of them should be fine to restore to if they are all indeed signed,
		# BUT we should prioritize (while restoring) the newest version
		# TODO
		return firmware_map

# end class IMobileDevice

# PROCESS MANAGER class - a ThreadPoolExecutor thing that creates and manages restore jobs on threads
class IMDRestoreManager:
	# constructor
	def __init__(self, *, max_workers=None):
		# ECID => (Future, DeviceID, IMDRestoreManager.Job)
		self.jobs = {}

		# ...
		if not max_workers:
			max_workers = 16
		self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="immrestore_")

	# destructor
	def __del__(self):
		self._executor.shutdown(cancel_futures=True)

	# __enter__ and __exit__ are used to allow a class to be used in a with statement
	# 

	def submit_job(self, device: Device):
		# executor.submit(...).add_done_callback(fn)
		# fn - callback with the future itself as the only argument
		# - if the function returns Device, argument will be Device
		#   (i think)
		if device.identifier.ecid in self.jobs:
			term.print_warning("Already a known job for this device! %s" % device.identifier)
			# return None

		future = self._executor.submit((restorejob := IMDRestoreManager.Job(device.identifier)).run, device)
		future.add_done_callback(restorejob.on_completed)

		self.jobs[device.identifier.ecid] = (future, device.identifier, restorejob)

		return future

	def print_job_summary(self, *, returnonly=False):
		resp = []
		for ecid in self.jobs:
			resp.append(f"ECID: {ecid} - {'restoring' if self.jobs[ecid][0].running() else 'not active'}")
		if returnonly:
			return "\n".join(resp)

		for r in resp:
			print(r)

	def clear_finished_jobs(self):
		for ecid in (keys := list(self.jobs.keys())):
			if self.jobs[ecid].running() and ecid in self.jobs:
				del self.jobs[ecid]

	@property
	def has_running_jobs(self):
		for ecid in self.jobs:
			if self.jobs[ecid][0].running():
				return True
		return False

	@property
	def running_jobs_count(self):
		c = 0
		for ecid in self.jobs:
			if self.jobs[ecid][0].running():
				c += 1
		return c

	# unneeded; jobs are started when submitted if there are available workers to take them
	# 
	# def start(self):
	# 	for future in as_completed([self.jobs[devid] for devid in self.jobs]):
	# 		try:
	# 			future.result()
	# 		except Exception as e:
	# 			term.print_error(f"{e}")
		
	# 	# at this point, all done!
	# 	pass

	# class for jobs? or a simple function with DeviceID arg?
	class Job:
		def __init__(self, deviceid: DeviceID, *, erase_restore=True):
			self._device_id = deviceid
			self._device = None

			self._running = False
			self._result = None
			# ...
			self._logfile = None
			self._returncode = None
			self._starttime = None
			self._endtime = None
		
		def run(self, device: Device):
			self._device = device
			self._running = True

			term.print_warning("* Beginning restore operation for device with %s" % device.identifier)
			self._starttime = time()

			if device.bootmode == "normal":
				if (entered_recovery := device.enter_recovery(wait=True)):
					# hell ya
					pass
				else:
					# hell nah
					term.print_error(f"* Unable to put device {device.identifier} in recovery mode; try doing it manually and attempt the restore again")
					self._returncode = -1
					return False

			self._logfile = normalize_path(IMobileDevice.LOG_PATH, f"restore-{device.serial_number}-{strftime("%H.%M.%S")}.log")

			# begin restore process
			self._returncode = device.restore(logfile=self._logfile, suppress_msgs=True)

			self._running = False
			self._endtime = time()

			return device

		def on_completed(self, future):
			if future.cancelled():
				term.print_error("* Restore cancelled for %s; this device may be in an unusable state!" % self.device_id)
			elif future.done():
				if self.returncode == 0:
					term.print_labelled("* Restore finished", self.device_id, color="green")
				else:
					term.print_labelled("* Restore FAILED", self.device_id, color="yellow")
					term.print("Please check the logfile for this restore process to see what went wrong:")
					term.print("  " + self._logfile)

				try:
					self._result = future.result(timeout=5)
				except TimeoutError as te:
					term.print_error("* Restore may have finished, but unable to get result?")

		@property
		def running(self):
			return self._running

		@property
		def device_id(self):
			return self._device_id
		
		@property
		def device(self):
			return self._device

		@property
		def result(self):
			return self._result

		@property
		def returncode(self):
			return self._returncode
		
		@property
		def logfile(self):
			return self._logfile

		@property
		def starttime(self):
			return self._starttime

		@property
		def endtime(self):
			return self._endtime

		@property
		def duration(self):
			if not self.starttime or not self.endtime:
				return None

			return f"{round(int(self.endtime - self.starttime) / 60, 2)} minutes"
	# end class IMDRestoreManager.Job
# end class IMDRestoreManager


# ====================================================
# user-interactive functions and menus
# 

class IMDApp:
	# devices from most recent scan are remembered here
	active_devices = []
	firmware_map = {}

	# list of device IDs to be erased when invoked
	restore_queue = []

	# restore job manager
	restorer: IMDRestoreManager = None

	# internal helper methods and debug functions
	# =====================================================================
	
	# def on_print_option(index: int, highlighted: bool, opt: object):
	#   ... where index = the index of the option being printed
	#   	      highlighted = whether this index is the selected one
	#   	      opt = choices[index]
	def _device_menu_print_option(index, highlighted, opt, marked=None):
		fields = ()
		format_str = "%s [%s / %s]"
		if marked is None:
			format_str = f"  {format_str}"

		if opt.is_restoring:
			fields = (
				opt["ModelName"],
				opt["SerialNumber"],
				"RESTORING"
			) # ["ModelName", "SerialNumber", "DeviceName"]
		else:
			if opt["BootMode"] == "Normal":
				fields = (
					opt["ModelName"],
					opt["SerialNumber"],
					opt["DeviceName"]
				) # ["ModelName", "SerialNumber", "DeviceName"]
			else:
				fields = (
					opt["ModelName"],
					opt["SerialNumber"],
					"Recovery Mode"
				) # ["ModelName" inferred from "ProductType", "SerialNumber", Recovery Device]
			
		if highlighted:
			if marked is not None:
				if bool(marked):
					format_str = f"[X] {format_str}"
				else:
					format_str = f"[ ] {format_str}"
			
			term.print_highlighted(format_str % fields)
		else:
			if marked is not None:
				if bool(marked):
					format_str = f"[X] {format_str}"
				else:
					format_str = f"[ ] {format_str}"
			
			term.print(format_str % fields)

	# debug methods or for interactive debugging
	@classmethod
	def _show_device_ipsws(cls, devices):
		for dev in devices:
			if (firmware := dev.get_restore_ipsw_filename()):
				term.print_labelled(dev.model_name, firmware, color="green")
			else:
				term.print_labelled(dev.model_name, "NO FIRMWARE FOUND IN PATH", color="red")

	@classmethod
	def _submit_restore_jobs(cls, devices):
		if not cls.restorer:
			cls.restorer = IMDRestoreManager()
		for dev in devices:
			cls.restorer.submit_job(dev)
		term.print_warning(f"* submitted {len(devices)} restore jobs")

	@classmethod
	def _check_restore_jobs(cls):
		if not cls.restorer:
			term.print_warning("* restorer object not yet created!")
			return

		for ecid in cls.restorer.jobs:
			job = cls.restorer.jobs[ecid]
			# => (Future, DeviceID, IMDRestoreManager.Job)
			term.print_labelled("- Device", job[1])
			term.print_labelled("  Status", f"{term.fgcolors["green"]}running{term._reset()}" if job[0].running() else f"{term.fgcolors["yellow"]}not active{term._reset()}")

	# user-facing menus and UI functions
	# =====================================================================
	# 
	@classmethod
	def show_qr_codes(cls, device):
		# first, get all the codes for this device:
		# serial, imei (1 and 2 if present), iccid, chipid, linq item code
		# 
		# key: device property
		# value: (display label, code string)
		qrcodes = {}

		if device.serial_number:
			qrcodes["serial_number"] = (f"S/N: {device.serial_number}", _qrencode(device.serial_number, return_string=True))
		if device.is_cellular_capable:
			if device.imei_1:
				qrcodes["imei_1"] = (f"IMEI: {device.imei_1}", _qrencode(device.imei_1, return_string=True))
			if device.imei_2:
				qrcodes["imei_2"] = (f"IMEI2: {device.imei_2}", _qrencode(device.imei_2, return_string=True))
			if device.iccid_1:
				qrcodes["iccid_1"] = (f"ICCID: {device.iccid_1}", _qrencode(device.iccid_1, return_string=True))
			if device.iccid_2:
				qrcodes["iccid_2"] = (f"ICCID2: {device.iccid_2}", _qrencode(device.iccid_2, return_string=True))
		if device.chipid:
			qrcodes["chipid"] = (f"ChipID: {device.chipid}", _qrencode(device.chipid, return_string=True))
		if device.udid:
			qrcodes["udid"] = (f"UDID: {device.udid}", _qrencode(device.udid, return_string=True))
		if device.linq_im_code:
			qrcodes["linq_im_code"] = (f"LINQ Item Code: {device.linq_im_code}", _qrencode(device.linq_im_code, return_string=True))

		# open a flip-book type interface that users can scroll through the available codes with
		running = True
		index = 0
		keys = list(qrcodes.keys())
		while running:
			term.screen("QR Code Viewer - Device: %s (%d of %d)" % (device.model_name, index + 1, len(keys)))

			current_code = qrcodes[keys[index]]
			label = [s.strip() for s in current_code[0].split(":")]
			qr = current_code[1]

			term.print_labelled("LEFT/RIGHT", "Switch code")
			term.print_labelled("X/Q", "Back")
			print()
			term.print_labelled(label[0], label[1])
			print(qr)
			print()
			print("             " + term._color(fg="lightblue") + "<--" + term._reset() + "   " + ("%d of %d" % (index + 1, len(keys))) + "   " + term._color(fg="lightblue") + "-->" + term._reset())

			handled = False
			while not handled:
				sleep(0.05)
				kp = term.get_keypress()
				handled = True
				match kp:
					case "left":
						index -= 1
						if index < 0:
							index = len(keys) - 1
					case "right":
						index += 1
						if index >= len(keys):
							index = 0
					case "x" | "q" | "backspace":
						running = False
					case _:
						handled = False
	# end show_qr_codes()

	@classmethod
	def print_recovery_device_summary(cls, device):
		term.screen("Summary: %s - %s (recovery mode)" % (device.model_name, device.serial_number))
		term.print_labelled("    Device", f"{device.model_name} (recovery mode)", color="green")
		term.print_labelled("      Name", device.name)
		term.print_labelled("     Model", f"{device.product_type}")
		if (chip := ChipID.lookup(device.chipid)):
			term.print_labelled("      Chip", f"{chip.name} ({chip.id})")
		else:
			term.print_labelled("      Chip", device.chipid)
		term.print_labelled("       S/N", device.serial_number)
		term.print_labelled("      UDID", device.udid)
		term.print_labelled("      ECID", device.ecid)
		term.print_labelled("      Boot", device.bootmode)
		print()
		term.print_warning("This device is in recovery mode.")

		print()

	@classmethod
	def print_device_summary(cls, device):
		if device.bootmode != "normal":
			# something different for recovery devices
			cls.print_recovery_device_summary(device)
			return

		term.screen("Summary: %s - %s" % (device.model_name, device.serial_number))
		term.print_labelled(f"    Device", f"{device.model_name} {device.model_storage} {device.model_color}", color="green")
		term.print_labelled("      Name", device.name)
		term.print_labelled("     Model", f"{device.sku} ({device.product_type})")
		if (chip := ChipID.lookup(device.chipid)):
			term.print_labelled("      Chip", f"{chip.name} ({chip.id})")
		else:
			term.print_labelled("      Chip", device.chipid)
		term.print_labelled("       S/N", device.serial_number)
		term.print_labelled("      UDID", device.udid)
		term.print_labelled("      ECID", device.ecid)
		if device.model_releasedate:
			term.print_labelled("  Released", f"{device.model_releasedate} ({int((age := divmod((date.today() - date(*map(int, device.model_releasedate.split("-")))).days, 365.25))[0])} year(s), {int(age[1])} day(s) ago)")

		print()
		term.print_labelled(f"  Software", f"{device.osname} {device.osversion}", color="green")
		term.print_labelled("     Build", device.osbuild)
		term.print_labelled("      Boot", device.bootmode)
		if device.icloud_locked:
			term.print_labelled("    iCloud", device.icloud_account)
		else:
			term.print_labelled("    iCloud", "not signed in")
		locale = device.get_locale_info()
		if locale:
			term.print_labelled("  Language", locale["Language"])

		print()
		term.print_labelled(f" LINQ code", device.linq_im_code, color="magenta")

		print()
		battery = device.get_battery_info()
		if battery:
			bat_status = "charging" if battery["BatteryIsCharging"] else ("plugged in" if battery["ExternalConnected"] else "discharging")
			term.print_labelled(f"   Battery", f"{battery["BatteryCurrentCapacity"]}% ({bat_status})", color="green")
			if battery["GasGaugeCapability"]:
				term.print_labelled("    Cycles", device.get_power_info()["CycleCount"])
		else:
			term.print_labelled(f"   Battery", "none", color="green")

		print()
		term.print_labelled(f"  Cellular", "yes" if device.is_cellular_capable else "no", color="green")

		def labelcolor(prop):
			return "lightblue" if prop is not None else "yellow"

		if device.is_cellular_capable:
			term.print_labelled("  Has pSIM", "yes" if device.has_psim_slot else "no")
			if device.has_psim_slot:
				term.print_labelled("      pSIM", "present" if device.has_psim_inserted else "not present")

			term.print_labelled("   Carrier", (str(device.carrier_1) if device.carrier_1 else "n/a") + (" (%s)" % device.carrier_id_1), color=labelcolor(device.carrier_1))
			term.print_labelled("      Line", str(device.get_line_number(1)) if device.phone_number_1 else "n/a", color=labelcolor(device.phone_number_1))
			term.print_labelled("      IMEI", str(device.imei_1) if device.imei_1 else "n/a", color=labelcolor(device.imei_1))
			term.print_labelled("     ICCID", str(device.iccid_1) if device.iccid_1 else "n/a", color=labelcolor(device.iccid_1))

			if device.imei_2:
				term.print_labelled(" Carrier 2", (str(device.carrier_2) if device.carrier_2 else "n/a")  + (" (%s)" % device.carrier_id_2), color=labelcolor(device.carrier_2))
				term.print_labelled("    Line 2", str(device.get_line_number(2)) if device.phone_number_2 else "n/a", color=labelcolor(device.phone_number_2))
				term.print_labelled("    IMEI 2", str(device.imei_2) if device.imei_2 else "n/a", color=labelcolor(device.imei_2))
				term.print_labelled("   ICCID 2", str(device.iccid_2) if device.iccid_2 else "n/a", color=labelcolor(device.iccid_2))	

		print()
	# end print_device_summary()

	# if Attribute.Domain.FACTORY_DISK_USAGE in self._domains:
	# 	get_storage_info() will have keys ["AmountDataAvailable", "AmountDataReserved", "CalendarUsage", "CameraUsage", "MediaCacheUsage", "PhotoUsage", "VoicemailUsage", "WebAppCacheUsage", "TotalDataAvailable", "TotalDataCapacity", "TotalDiskCapacity", "TotalSystemAvailable", "TotalSystemCapacity"]
	# else:
	# 	get_storage_info() will have keys ["TotalDataAvailable", "TotalDataCapacity", "TotalDiskCapacity", "TotalSystemAvailable", "TotalSystemCapacity"]
	@classmethod
	def print_storage_summary(cls, device):
		term.screen("Storage summary: %s - %s" % (device.model_name, device.serial_number))
		storage = device.get_storage_info()
		for key in storage:
			value = int(storage[key])
			friendly = f"{value} B"
			if value > 1000000000: # GB
				friendly = f"{round(value / 1000000000, 2)} GB"
			elif value > 1000000: # MB
				friendly = f"{round(value / 1000000, 2)} MB"
			elif value > 1000: # KB
				friendly = f"{round(value / 1000, 2)} KB"

			term.print_labelled("  %20s" % key, f" {friendly}")

		print()
		term.pause()

	@classmethod
	def view_device_apps(cls, device):
		show_user = True
		show_system = True
		show_services = False

		user_apps = device.get_installed_apps()
		system_apps = [app for app in device.get_system_apps() if not app.is_system_service_bundle()]
		system_services = [app for app in device.get_system_apps() if app.is_system_service_bundle()]

		# def on_print_option(index: int, highlighted: bool, opt: object):
		#   ... where index = the index of the option being printed
		#   	      highlighted = whether this index is the selected one
		#   	      opt = choices[index]
		def print_option(index, highlighted, opt):
			format_str = "%s [%s]"
			fields = (opt.name, opt.id)
			if highlighted:
				term.print_highlighted(format_str % fields)
			else:
				term.print(format_str % fields)

		hotkeys = {
			"s": "toggle-system",
			"u": "toggle-user",
			"v": "toggle-services"
		}
		running = True
		initial_index = 0
		while running:
			viewer_list = list(chain((user_apps if show_user else []), (system_apps if show_system else []), (system_services if show_services else [])))

			instructions = [
				f"S: {"hide" if show_system else "show"} system apps",
				f"U: {"hide" if show_user else "show"} user-installed apps",
				f"V: {"hide" if show_services else "show"} system service bundles",
				"X, BACKSPACE: back",
				"========================================================"
			]

			selection = term.menu("Installed apps/services:", viewer_list, title=f"{APP_NAME} [{device.model_name}] - App list", format_str="%s (%s)", on_print_option=print_option, initial_index=initial_index, return_index=True, quit_keys=["x", "backspace"], show_pages=False, instructions=instructions, hotkeys=hotkeys, allow_ctrlc=True)
			if not selection:
				running = False
			else:
				if type(selection) is tuple:
					# (selection_index when the key was pressed, the defined response, the key)
					initial_index = selection[0]

					match selection[1]:
						case "toggle-system":
							show_system = not show_system
							if not (show_system or show_user or show_services):
								show_system = True
						case "toggle-user":
							show_user = not show_user
							if not (show_system or show_user or show_services):
								show_user = True
						case "toggle-services":
							show_services = not show_services
							if not (show_system or show_user or show_services):
								show_services = True
				elif type(selection) is int:
					# selection index
					initial_index = selection
	# end view_device_apps()

	@classmethod
	def device_repl_loop(cls, device):
		if not REPLCompleter.has_dependencies_installed():
			term.screen("REPL Mode: %s - %s" % (device.model_name, device.serial_number))
			term.print_error("** Cannot start REPL mode")
			print()
			term.print("REPL interaction with the device depends on the 'readline' module")
			term.print("being available on your system.")

			if IMobileDevice.PLATFORM == "Windows":
				print()
				term.print("Run this command on Windows to install it:")
				term.print_msg("   pip install pyreadline3")
				print()

			term.pause(prompt="Press any key to return to the device menu...")

			return

		term.screen("REPL Mode: %s - %s" % (device.model_name, device.serial_number))
		term.print_msg("The selected device can be accessed with name 'device'.")
		term.print_warning("Type 'exit' and press ENTER to exit REPL mode")
		print()

		with REPLCompleter():
			while (expression := input("# ").strip()) != "exit":
				try:
					if expression == "":
						continue

					print(response := pformat(_ := eval(expression)))
					print()
					logger.info(response)
				except BaseException as exception:
					term.print_error(str(exception))
					print()
					logger.error(f"[!] Exception while evaluating expression: {str(expression)}", exc_info=exception)

	@classmethod
	def view_devices(cls, *, rescan=True, skip_inspect_prompt=False):
		if rescan:
			term.screen(f"{APP_NAME} - Scanning for devices, please wait...")

			cls._normal_devices = IMobileDevice.get_connected_devices()
			cls._recovery_devices = IMobileDevice.get_recovery_devices()
			cls.active_devices = list(chain(cls._normal_devices, cls._recovery_devices))

			for dev in cls.active_devices:
				dev.dump_info()

		if len(cls.active_devices) == 0:
			term.print_warning("* No devices were detected")
			print()
			term.print("If you have devices plugged in, make sure they are powered on and unlocked, or in recovery mode.")
			print()
			term.pause()
			return
		else:
			print()

		if rescan and not skip_inspect_prompt:
			term.screen(f"{APP_NAME} - Device scan complete")
			term.print_success("Detected %d device(s):" % len(cls.active_devices))
			for dev in cls.active_devices:
				term.print(f"- {dev["ModelName"]} (boot: {dev["BootMode"]}) - S/N: {dev["SerialNumber"]}")

			print()
			term.print_msg("Now that devices are scanned, you can wipe them.")
			term.print("Return to the main menu or proceed to inspection to access the Erase options.")
			print()

			if not term.input_yn("Inspect these devices?"):
				return
		
		instructions = [
			"L: rename        Q: show QRs       E: erase device(s)",
			"R: restart       B: boot recovery  C: rescan devices",
			"S: shutdown      N: boot normal    F: download firmware",
			"P: ping device   A: view apps      X: quit",
			"========================================================"
		]

		hotkeys = {
			"e": "erase",
			"r": "restart",
			"s": "shutdown",
			"q": "qrcodes",
			"b": "boot-recovery",
			"n": "boot-normal",
			"l": "rename",
			"c": "rescan",
			"f": "firmware",
			"x": "quit",
			"p": "ping",
			"a": "app-viewer",
			"#": "repl",
			"backspace": "quit"
		}

		running = True
		while running:
			selection = term.menu("Select a device and press ENTER for more information", cls.active_devices, title=f"{APP_NAME} ({APP_VERSION}) - Connected devices", on_print_option=cls._device_menu_print_option, show_pages=False, clear_on_finish=False, instructions=instructions, hotkeys=hotkeys, format_str="  %s [%s]", format_fields=["ModelName", "SerialNumber"])
			if selection:
				if type(selection) is Device:
					refreshed = True
					while refreshed:
						refreshed = False
						# show summary
						cls.print_device_summary(selection)

						# term.pause(prompt="Press any key to return to the device menu...")

						# possibly enter a keyboard wait loop, and show Storage Info with the S key?
						# selection.get_storage_info()
						print("Press any key to return to the device menu...")
						# sleep(0.5)
						summary_key = term.get_keypress()
						match summary_key:
							case "s":
								if selection.bootmode == "normal":
									cls.print_storage_summary(selection)
							case "q":
								if selection.bootmode == "normal":
									term.screen("Generating QR codes...")
									cls.show_qr_codes(selection)
							case "r":
								term.screen("Refreshing device info...")
								term.print_msg("Connecting to device, please wait...")
								if selection.ping(refresh=True):
									refreshed = True
								else:
									term.print_warning("Connection to the device was lost.")
									term.print("Make sure the device is plugged in and unlocked, or in recovery mode, and try again.")
									print()
									term.pause(prompt="Press any key to return to the device menu...")
							case "#": # REPL mode
								cls.device_repl_loop(selection)
				elif type(selection) is tuple:
					# (selection_index when the key was pressed, the defined response, the key)
					device = cls.active_devices[selection[0]]

					match selection[1]:
						case "erase":
							devselection = cls.select_devices("Choose one or more devices to ERASE and RESTORE to factory settings.", "Restore device(s)", confirm_with_c=True, hazard_menu=True)
							if not devselection:
								continue

							if cls.confirm_wipe_devices(devselection):
								logger.info("user confirmed wipe of devices")
								
								for dev in devselection:
									cls.restorer.submit_job(dev)

								print()
								term.print_success("* Restore jobs have been submitted *")
								print()

								if term.input_yn("Inspect running jobs?"):
									cls.view_restore_jobs()
									running = False
						case "restart":
							if device.is_restoring:
								term.modalalert("Invalid action", f"The device {device.model_name} [{device.serial_number}] is currently being restored and cannot be rebooted.", buttons=term.ModalButtons.OK, clear_on_start=False, allow_esc_cancel=True)
								continue

							if term.modalalert("Confirm action", f"The device {device.model_name} [{device.serial_number}] will be restarted.", buttons=term.ModalButtons.OKCANCEL, default_button=1, clear_on_start=False, allow_esc_cancel=True):
								device.restart()
						case "shutdown":
							if device.is_restoring:
								term.modalalert("Invalid action", f"The device {device.model_name} [{device.serial_number}] is currently being restored and cannot be shut down.", buttons=term.ModalButtons.OK, clear_on_start=False, allow_esc_cancel=True)
								continue

							if term.modalalert("Confirm action", f"The device {device.model_name} [{device.serial_number}] will be shut down.", buttons=term.ModalButtons.OKCANCEL, default_button=1, clear_on_start=False, allow_esc_cancel=True):
								device.shutdown()
						case "qrcodes":
							term.screen("Generating QR codes...")
							cls.show_qr_codes(device)
						case "boot-recovery":
							if device.is_restoring:
								term.modalalert("Invalid action", f"The device {device.model_name} [{device.serial_number}] is currently being restored and cannot be rebooted.", buttons=term.ModalButtons.OK, clear_on_start=False, allow_esc_cancel=True)
								continue

							if term.modalalert("Confirm action", f"The device {device.model_name} [{device.serial_number}] will enter Recovery Mode.", buttons=term.ModalButtons.OKCANCEL, default_button=1, clear_on_start=False, allow_esc_cancel=True):
								device.enter_recovery()
						case "boot-normal":
							if device.is_restoring:
								term.modalalert("Invalid action", f"The device {device.model_name} [{device.serial_number}] is currently being restored and cannot be rebooted.", buttons=term.ModalButtons.OK, clear_on_start=False, allow_esc_cancel=True)
								continue

							action = "restart" if device.bootmode == "normal" else "restart into normal user mode"
							if term.modalalert("Confirm action", f"The device {device.model_name} [{device.serial_number}] will {action}.", buttons=term.ModalButtons.OKCANCEL, default_button=1, clear_on_start=False, allow_esc_cancel=True):
								if device.bootmode == "normal":
									device.restart()
								else:
									device.exit_recovery()
						case "rename":
							if device.is_restoring:
								term.modalalert("Invalid action", f"The device {device.model_name} [{device.serial_number}] is currently being restored and cannot be renamed.", buttons=term.ModalButtons.OK, clear_on_start=False, allow_esc_cancel=True)
								continue

							if device.bootmode != "normal":
								term.modalalert("Invalid action", f"The device {device.model_name} [{device.serial_number}] is in Recovery Mode and cannot be renamed.", buttons=term.ModalButtons.OK, clear_on_start=False, allow_esc_cancel=True)
								continue

							term.screen("Rename device %s [%s]" % (device.name, device.model_name))
							term.print_msg("* Press CTRL-C to cancel")
							print()
							newname = term.input("Enter new device name:", device.name, allow_ctrlc=True)
							if not newname:
								continue

							if device.set_name(newname):
								term.print_success("Device renamed successfully")
							else:
								term.print_warning("Rename operation may not have worked...")

							print()
							term.pause()
						case "rescan":
							running = False
							cls.view_devices(rescan=True, skip_inspect_prompt=True)
						case "repl":
							cls.device_repl_loop(device)
						case "ping":
							term.screen(f"{APP_NAME} - Pinging device {device.model_name} - {device.serial_number or device.ecid}")
							term.print_msg(f"Searching for device {device.serial_number or device.ecid}")
							if device.ping():
								term.print_success("This device is currently connected.")
							else:
								term.print_warning("This device is no longer connected or accessible.")

								# remove from active devices list?
							term.pause()
						case "app-viewer":
							if device.is_restoring:
								term.modalalert("Invalid action", f"The device {device.model_name} [{device.serial_number}] is currently being restored; apps cannot be viewed.", buttons=term.ModalButtons.OK, clear_on_start=False, allow_esc_cancel=True)
								continue

							if device.bootmode != "normal":
								term.modalalert("Invalid action", f"The device {device.model_name} [{device.serial_number}] is in Recovery Mode; apps cannot be viewed", buttons=term.ModalButtons.OK, clear_on_start=False, allow_esc_cancel=True)
								continue

							term.screen("Scanning for apps...")
							try:
								cls.view_device_apps(device)
							except Exception as e:
								# IndexError thrown when ideviceinfo call fails
								term.modalalert("Error: Unable to fetch apps", "There was an issue retrieving an apps list from the device; possibly it has not completed first-boot setup yet, or it is no longer connected to or accessible by your system.", clear_on_start=False, allow_esc_cancel=True)
						case "firmware":
							# first, check if there is firmware present for device and summarize it
							term.screen("Searching for device firmware")
							term.print_msg(f"Locating firmware for model {device.model_name}...")
							term.print_labelled("  Search path", IMobileDevice.get_ipsw_path())
							localfirm = IMobileDevice.detect_firmwares_for_devices([device])[device.product_type]

							if len(localfirm) > 0:
								key = list(localfirm.keys())[0]
								firmware = localfirm[key]

								cls.firmware_map[key] = firmware

								# firmware found; check for updates
								term.print_success("Found signed firmware for %s:" % device.model_name)
								term.print_msg("    %s %s (%s)" % (firmware["osname"], firmware["version"], firmware["build"]))
								term.print_labelled("    Location", os.path.split(firmware["fullpath"])[0])
								term.print_labelled("    Filename", key)
								term.print_labelled("    Size", f"{round(firmware["filesize"] / 1024 / 1024 / 1024, 2)} GB")
								term.print_msg("    Compatible with:")
								for dn in firmware["device_names"]:
									print("    - %s" % dn)
								
								print()
								if not term.input_yn("Download additional firmware for this device?", False):
									continue
							else:
								term.print_warning("No signed firmware detected for device %s (%s)" % (device.model_name, device.product_type))
								# term.print_labelled("    Path", IMobileDevice.get_ipsw_path())

								print()
								if not term.input_yn("Download firmware for this device?"):
									continue

							# then, prompt if user wants to download anyway
							# (if no local firmware, go straight to download prompt)
							dl = {"device": device.model_name, "id": device.product_type}
							dl["firmware"] = IPSWApp.menu_select_firmware(IMobileDevice.ipsw, dl, allow_ctrlc=True)
							if dl["firmware"]:
								IPSWApp.action_download_multiple(IMobileDevice.ipsw, [dl])
						case "quit":
							running = False
					pass
				else:
					term.print_error("* unknown response type?")
					print(selection)

					term.pause()
			else:
				running = False
	# end view_devices()
	
	# returns List of Devices selected by user from cls.active_devices
	# 
	@classmethod
	def select_devices(cls, prompt, title=None, *, confirm_with_c=False, hazard_menu=False):
		if not title:
			title = f"{APP_NAME} ({APP_VERSION}) - Select device(s)"
		else:
			title = f"{APP_NAME} ({APP_VERSION}) - {title}"

		instructions = [
			f"Press SPACE to select/unselect a device",
			f"A: select all / D: deselect all / I: invert selection",
			f"",
			f"Press {"C" if confirm_with_c else "ENTER"} to confirm selection",
			f"Press X or BACKSPACE to go back",
			"========================================================"
		]

		titlebg = "gray"
		titlefg = "black"

		if hazard_menu:
			titlebg = "red"
			titlefg = "white"

		hotkeys = {
			"a": "select-all",
			"d": "deselect-all",
			"i": "invert-selection"
		}
		if confirm_with_c:
			hotkeys["c"] = "confirm"

		running = True
		initial_index = []
		while running:
			# initial_index = 0
			selection = term.menu(prompt, cls.active_devices, title=title, on_print_option=cls._device_menu_print_option, show_pages=False, instructions=instructions, hotkeys=hotkeys, initial_index=initial_index, quit_keys=["x", "q", "backspace"], format_str="  %s [%s]", format_fields=["ModelName", "SerialNumber"], multiselect=True, titlebar_bg=titlebg, titlebar_fg=titlefg, disable_enter_action=True)
			if not selection:
				running = False
			else:
				if type(selection) is tuple:
					# selection: (option that is highlighted when hotkey is pressed, the defined hotkey response, the key, [list of selected indices])
					match selection[1]:
						case "confirm":
							return [cls.active_devices[i] for i in selection[3]]
						case "select-all":
							initial_index = list(range(len(cls.active_devices)))
						case "deselect-all":
							initial_index = []
						case "invert-selection":
							initial_index = []
							for i in range(len(cls.active_devices)):
								if i not in selection[3]:
									initial_index.append(i)
				elif type(selection) is list:
					# selection: List of Device objects
					# return selection
					# 
					# return NOTHING, do NOT return on ENTER
					pass
				else:
					term.print_error("* unknown response type?")
					print(selection)

					term.pause()
	# end select_devices()

	# return True if the wipe can proceed
	# 
	# also confirms that all required firmwares are present
	# (irecovery will auto-download as needed, so the user can
	# skip the pre-restore download step if desired)
	@classmethod
	def confirm_wipe_devices(cls, devices) -> bool:
		term.screen(f"{APP_NAME} - Confirm wipe devices [1/2]", barcolor="red", textcolor="white")
		term.print_warning("The following devices will be erased and restored:")

		def summarize_actions():
			for device in devices:
				term.print_labelled("- Device", f"{device.model_name} (S/N: {device.serial_number})")

				localfirm = IMobileDevice.detect_firmwares_for_devices([device])[device.product_type]
				if len(localfirm) > 0:
					# found a firmware for this device!
					key = list(localfirm.keys())[0]
					firmware = localfirm[key]

					cls.firmware_map[key] = firmware
					term.print_labelled("  Restoring to", f"{firmware["osname"]} {firmware["version"]}")
					print("  IPSW path: %s" % firmware["fullpath"])
				else:
					# no restorable firmware found for this device
					# will need to download the latest
					firmware_latest = IMobileDevice.ipsw.get_latest_firmware(device.product_type)
					term.print_labelled("  Downloading and installing", f"{device.osname} {firmware_latest["version"]}")
					# imobiledevice lib will do this as needed, no further download action needed here

		summarize_actions()

		print()
		term.print_msg("Please review the output above to make sure that this is what you want.")
		print("You will receive a final confirmation prompt next.")
		print()
		if not term.input_yn("Is this information correct?", False):
			return False

		term.screen(f"{APP_NAME} - Confirm wipe devices [2/2]", barcolor="red", textcolor="white")
		term.print_warning("The following devices will be erased and restored:")

		summarize_actions()

		print()
		term.print_msg("Please review the output above to make sure that this is what you want.")
		term.print_warning("If you proceed, THESE DEVICES WILL BE ERASED AND RESTORED.")
		print()
		verification_can = random.choice([
			"Lobotomize me, captain!",
			"Yes, do as I say!",
			"What's an IPSW?",
			"Restore machine go brrr",
			"iOS go bye bye",
			"Time to eat your Apples!",
			"Glory in the name of Jobs"
		])
		term.print_labelled("To proceed, type the following phrase", verification_can)

		if term.input("> ").strip() == verification_can.strip():
			return True

		print()
		term.print_warning("* Confirmation check failed; aborting restore process")
		term.pause()
		return False
	# end confirm_wipe_devices()
	 
	@classmethod
	def view_restore_jobs(cls):
		title = f"{APP_NAME} ({APP_VERSION}) - Restore jobs"

		instructions = [
			"Select a job for more info or:",
			"R: refresh list        C: cancel job",
			"D: restart job         P: purge finished jobs",
			"L: view job logfile    X,Q,BACKSPACE: go back",
			"==============================================="
		]

		hotkeys = {
			"r": "refresh",
			"c": "cancel",
			"d": "restart",
			"p": "purge-finished",
			"l": "view-log"
		}

		quit_keys = ["x", "q", "backspace"]
		# def on_print_option(index: int, highlighted: bool, opt: object):
		#   ... where index = the index of the option being printed
		#   	      highlighted = whether this index is the selected one
		#   	      opt = choices[index]
		def print_option(index, highlighted, opt):
			# opt: str => device ECID (key for the jobs list)
			job = cls.restorer.jobs[opt]
			# (Future, DeviceID, IMDRestoreManager.Jobs)
			device_id = job[1]

			option = job[2].device.model_name
			if not device_id.serial:
				option = f"{option} (ECID: {device_id.ecid})"
			else:
				option = f"{option} (S/N: {device_id.serial})"

			status = "not active"
			if job[0].running():
				status = "running"
			elif job[0].cancelled():
				status = "cancelled"
			elif job[0].done():
				if job[2].returncode == 0:
					status = "done [success]"
				else:
					status = f"done [failed: {job[2].returncode}]"

			option = f"{option} - {status}"

			if highlighted:
				term.print_highlighted(option)
			else:
				term.print(option)

		running = True
		initial_index = 0
		while running:
			if len(cls.restorer.jobs) == 0:
				running = False
				term.screen(title)
				term.print_msg("No restore jobs are in the queue.")
				term.pause()
				continue

			selection = term.menu("Current jobs in restore queue:", list(cls.restorer.jobs.keys()), title=title, initial_index=initial_index, on_print_option=print_option, instructions=instructions, hotkeys=hotkeys, quit_keys=["x", "q", "backspace"], clear_on_finish=False)
			if not selection:
				running = False
				continue

			if type(selection) is tuple:
				# => (selection_index when the key was pressed, the defined response, the key)
				ecid = list(cls.restorer.jobs.keys())[selection[0]]
				job = cls.restorer.jobs[ecid]
				# => (Future, DeviceID, IMDRestoreManager.Job)
				match selection[1]:
					case "refresh":
						initial_index = selection[0]
					case "cancel":
						if not job[0].done() and term.modalalert("Confirm job cancel", "Are you SURE you want to cancel this restore job?  It may leave the device in an unusable state.", clear_on_start=False, buttons=term.ModalButtons.YESNO, default_button=1, allow_esc_cancel=True, allow_ctrlc=True):
							# cancel da job
							term.modalalert("Cancelling job...", "This can take up to 30 seconds.", clear_on_start=False, no_user_interaction=True)
							cancelled = job[0].cancel()
					case "restart":
						if job[2].device and job[0].done():
							if term.modalalert("Confirm job restart", "Are you SURE you want to restart this restore job?  This may not always succeed.", clear_on_start=False, buttons=term.ModalButtons.YESNO, default_button=1, allow_esc_cancel=True, allow_ctrlc=True):
								term.modalalert("Cancelling existing job...", "This can take up to 30 seconds.", clear_on_start=False, no_user_interaction=True)
								cancelled = job[0].cancel()
								term.modalalert("Restarting job...", "The restore job is being restarted.")
								cls.restorer.submit_job(job[2].device)
						else:
							term.modalalert("Unable to restart", "An error occurred while attempting to restart this job.  Quit this script and try again.")
					case "purge-finished":
						for i in range(len(keys := list(cls.restorer.jobs.keys()))):
							if cls.restorer.jobs[(key := keys[i])][0].done():
								del cls.restorer.jobs[key]

						if initial_index >= len(cls.restorer.jobs):
							initial_index = 0
					case "view-log":
						# textreader(title, filename, *, background_color, text_color, titlebar_bg, titlebar_fg, use_pageupdown, use_homeend_scrolling)
						term.textreader(f"Restore log: S/N {job[2].device.serial_number}", job[2].logfile, background_color=None, text_color=None, titlebar_bg="red", titlebar_fg="white", use_pageupdown=True, use_homeend_scrolling=True)
			elif type(selection) is str:
				# ECID (the menu choices are a list of keys)
				job = cls.restorer.jobs[selection]
				# => (Future, DeviceID, IMDRestoreManager.Job)
				device = job[2].device

				term.screen(f"Restore job for ECID: {selection}")
				term.print_labelled("   Device", device.model_name or device.product_type, color="green")
				term.print_labelled("       SN", device.serial_number)
				term.print_labelled("     ECID", device.ecid)
				term.print_labelled("     UDID", device.udid)
				print()
				term.print_labelled("   Status", "running" if job[0].running() else ("cancelled" if job[0].cancelled() else ("done" if job[0].done() else "not active")))
				term.print_labelled("      Log", job[2].logfile)
				if job[2].starttime:
					term.print_labelled("  Started", strftime("%D %I:%M:%S %p", localtime(job[2].starttime)))

				print()
				if job[0].done():
					term.print_labelled(" Exitcode", job[2].returncode)
					term.print_labelled("   Result", "success" if job[2].returncode == 0 else "error")
					term.print_labelled(" Finished", strftime("%D %I:%M:%S %p", localtime(job[2].endtime)))
					term.print_labelled(" Duration", job[2].duration)

					print()

				term.pause()
			else:
				term.print_warning(f"* unknown response type: {selection}")
				term.pause()
	# end view_restore_jobs()
	

	# main entry point
	# =====================================================================
	# 
	@classmethod
	def main(cls) -> int | None:
		IMobileDevice.initialize()

		cls.active_devices.clear()
		cls.firmware_map.clear()
		cls.restore_queue.clear()
		cls.restorer = IMDRestoreManager()

		menu_title = f"{APP_NAME} - {APP_VERSION} ({APP_DATE})"

		# user warning
		running = term.modalalert("Important message", f"This script uses libimobiledevice to restore and manage devices.\n\nlibimobiledevice is an open-source programming library for managing, querying, and restoring Apple mobile devices.  As such, it can be a destructive tool that can PERMANENTLY ERASE data from connected devices.  Please double-check what you are about to do before doing it!\n\nAlso, this script is in development and may not fully work.  Be warned.\n\nSelect OK to confirm that you understand and wish to proceed.", buttons=term.ModalButtons.OKCANCEL, default_button=1, background_title=menu_title, background_color="blue", allow_esc_cancel=True)

		ran = False
		while running:
			default_choice = 0

			ran = True
			choices = [
				{"label": "Scan for devices", "function": cls.view_devices},
				{"label": "View devices from previous scan", "function": "view", "requires_scan": True},
				{"label": "Change firmware path [%s]" % IMobileDevice.get_ipsw_path(), "function": IMobileDevice.set_ipsw_path},
				{"label": "Download or manage device firmwares", "function": IMobileDevice.manage_ipsw},
				{"label": "View restore job queue", "function": cls.view_restore_jobs, "requires_jobs": True},
				{"label": "ERASE DEVICES - idevicerestore", "function": "wipe", "requires_scan": True},
				{"label": "ERASE DEVICES - cfgutil (macOS only)", "function": "wipe-appl", "requires_scan": True, "platform": "Darwin"},
				{"label": "Help / About", "function": "help"},
				{"label": "Exit", "function": "exit"}
			]
			instructions = [
				"Scan for devices first to show the Restore options",
				"",
				"Enter the number of your choice and press ENTER.",
				"==================================================="
			]
			if len(cls.active_devices) > 0:
				instructions[0] = "Select the first option to rescan devices"
				default_choice = 1

			# filter choices depending on if a device scan has been done
			choices = [c for c in choices if ("requires_scan" not in c) or ("requires_scan" in c and len(cls.active_devices) > 0)]
			# filter choices depending on if there are jobs in the restore queue
			choices = [c for c in choices if ("requires_jobs" not in c) or ("requires_jobs" in c and len(cls.restorer.jobs) > 0)]
			# hide platform-limited options if needed
			choices = [c for c in choices if ("platform" not in c) or (c["platform"] == platform.system())]

			header = f"Last scan: found {len(cls.active_devices)} devices"

			selection = term.numbermenu(header, choices, default_choice, title=menu_title, format_str="%s", format_fields=["label"], instructions=instructions)
			# selection = term.menu(header, choices, title=f"{APP_NAME} - {APP_VERSION}", format_str="%s", format_fields=["label"], show_pages=False, instructions=instructions)
			if selection:
				if callable(selection["function"]):
					selection["function"]()
				elif type(selection["function"]) is str:
					match selection["function"]:
						case "exit":
							running = False
						case "wipe":
							devselection = cls.select_devices("Choose one or more devices to ERASE and RESTORE to factory settings.", "Restore device(s)", confirm_with_c=True, hazard_menu=True)
							if not devselection:
								continue

							if cls.confirm_wipe_devices(devselection):
								logger.info("user confirmed wipe of devices")
								
								for dev in devselection:
									cls.restorer.submit_job(dev)

								print()
								term.print_success("* Restore jobs have been submitted *")
								print()

								if term.input_yn("Inspect running jobs?"):
									cls.view_restore_jobs()
						case "wipe-appl":
							if platform.system() != "Darwin":
								term.screen(f"{APP_NAME} - {APP_VERSION}")
								term.print_error("* Feature not available")
								print()
								term.print("This feature requires macOS, and Apple Configurator Automation Tools.")
								print()
								term.pause()
								continue

							pass
						case "view":
							cls.view_devices(rescan=False)
						case "help":
							term.textreader(f"{APP_NAME} - Help / About", "in-app-help.txt", replacements={"app_name": APP_NAME})
			else:
				running = False

		term.clear()
		if not ran:
			term.print_error("* User cancelled")
	# end main()
# end class IMDApp


if __name__ == "__main__":
	if not os.path.exists(IMobileDevice.LOG_PATH):
		os.makedirs(IMobileDevice.LOG_PATH)

	logger = logging.getLogger(__name__)
	# start the logger
	log_handlers = [
		logging.FileHandler(f"{IMobileDevice.LOG_PATH}/main-imobilemanager-{strftime("%H.%M.%S")}.log", mode="w")
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
	# IMobileDevice.initialize()
	IMDApp.main()