# TODO:
# 
# option 4: delete older firmwares, if multiple versions exist for a device and the older ones are no longer being signed
# 
import json, os, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from hashlib import file_digest
from urllib.error import HTTPError
from urllib.request import urlopen

from jb93term import Terminal as term

APP_NAME = "IPSWApp"
APP_VERSION = "v3.1"
APP_DATE = datetime.fromtimestamp(os.path.getmtime(sys.argv[0])).strftime("%Y-%m-%d %H:%M:%S")

# make sure pycurl is installed
import importlib.util
pycurl_spec = importlib.util.find_spec("pycurl")
if pycurl_spec is None:
	term.print_error("[IPSW] dependency missing!\n** pycurl module is not installed; please run this command:")
	print("  pip install pycurl")
	term.pause()
	sys.exit(-5)
import pycurl

# and tqdm (if not found, show some other form of progress)
tqdm_spec = importlib.util.find_spec("tqdm")
if tqdm_spec is None:
	term.print_error("[IPSW] dependency missing!\n** tqdm module is not installed; please run this command:")
	print("  pip install tqdm")
	term.pause()
	sys.exit(-5)
from tqdm import tqdm

# and requests (can this be replaced with a pycurl headers-only action? that's all it's in here for)
requests_spec = importlib.util.find_spec("requests")
if requests_spec is None:
	term.print_error("[IPSW] dependency missing!\n** requests module is not installed; please run this command:")
	print("  pip install requests")
	term.pause()
	sys.exit(-5)
import requests

def normalize_path(*parts):
	return os.path.join(*parts).replace("\\", "/")

def get_sha256(file):
	with open(file, "rb") as f:
		return file_digest(f, "sha256").hexdigest()

class IPSW:
	DEBUG_PRINTING=False

	def __init__(self):
		self.device_names = {}
		self.baseurl = "https://api.ipsw.me/v4"

		self.refresh_device_list()
		self.queue = []
		self.device_info_cache = {}

		self.downloadpath = normalize_path(os.path.expanduser("~"), "Downloads", "iOS Firmware")
		if not os.path.exists(self.downloadpath):
			os.makedirs(self.downloadpath, exist_ok=True)

	def refresh_device_list(self):
		with open("device_names.json", "r") as f:
			self.device_names = json.loads(f.read())
		if IPSW.DEBUG_PRINTING:
			print("[refresh_device_list] Loaded %d device IDs" % len(self.device_names))

	def get_path(self):
		return self.downloadpath
	def set_path(self, p):
		self.downloadpath = p.replace("\\", "/")

	def clear_cache(self):
		self.device_info_cache.clear()

	def use_itunes_path(self):
		found = False
		for itunes_path in ("AppData/Roaming/Apple Computer/iTunes/iPhone Software Updates", "AppData/Local/Packages/AppleInc.iTunes_nzyj5cx40ttqa/LocalCache/Roaming/Apple Computer/iTunes/iPhone Software Updates"):
			if os.path.exists(normalize_path(os.path.expanduser("~"), itunes_path)):
				self.set_path(normalize_path(os.path.expanduser("~"), itunes_path))
				found = True

		if not found:
			pass
	
	def use_apconf_path(self):
		apconf_path = normalize_path(os.path.expanduser("~", "Library/Group Containers/K36BKF7T3D.group.com.apple.configurator/Library/Caches/Firmware"))

		if os.path.exists(apconf_path):
			self.set_path(apconf_path)
		else:
			pass

	def get_downloaded_ipswfiles(self, *, fullpaths=False):
		if fullpaths:
			return [os.path.join(self.downloadpath, f) for f in os.listdir(self.downloadpath) if os.path.isfile(os.path.join(self.downloadpath, f)) and f[-5:].lower() == ".ipsw" and f[0:2] != "._"]
		else:
			return [f for f in os.listdir(self.downloadpath) if os.path.isfile(os.path.join(self.downloadpath, f)) and f[-5:].lower() == ".ipsw" and f[0:2] != "._"]

	def get_devices_from_groupid(self, groupid):
		if groupid in self.device_names:
			return self.device_names[groupid].split(";")[1:]
		return None

	def get_group_for_deviceid(self, devid):
		for key in self.device_names:
			if "," not in key:
				if devid in self.device_names[key]:
					return key
		return devid

	def get_downloaded_firmwares(self):
		ipsw_files = self.get_downloaded_ipswfiles()

		# device => [firmware, versions, found, ...]
		detected_firmwares = {}

		for f in ipsw_files:
			f_parts = f.split("-")[-1].split("_")
			f_version = f_parts[-3]
			f_devices = f_parts[:-3][0].replace(",i", ";i").split(";")

			for d in f_devices:
				if d not in self.device_names:
					# could be an iPhone/iPad with a different filename format
					# ex: iPad_Air_M2_26.4.1_23E254_Restore.ipsw
					#    ^iPad_Air_M2^
					# need to get any device that this identifier covers, the firmware should be the same
					groupid = "_".join(f_parts[:-3])

					if groupid in self.device_names:
						#gd_ids = self.device_names[groupid].split(";")[1:]

						#gd = gd_ids[0]

						if groupid not in detected_firmwares:
							detected_firmwares[groupid] = []
						detected_firmwares[groupid].append(f_version)
						# detected_firmwares[groupid].append({
						# 	"version": f_version,
						# 	"filename": f
						# 	})
					else:
						term.print_error("* unrecognized firmware file: %s" % f)
					pass
				else:
					# d = device identifier like iPhone1,1
					if d not in detected_firmwares:
						detected_firmwares[d] = []
					detected_firmwares[d].append(f_version)

		return detected_firmwares

	def get_downloaded_firmwares_dict(self):
		ipsw_files = self.get_downloaded_ipswfiles()

		# device => [firmware, versions, found, ...]
		detected_firmwares = {}

		# filename => {path: , version: , deviceid or groupid: , filesize, moddate}
		detected_files = {}

		for f in ipsw_files:
			detected_files[f] = {}

			f_parts = f.split("-")[-1].split("_")
			f_version = f_parts[-3]
			f_build = f_parts[-2]
			f_devices = f_parts[:-3][0].replace(",i", ";i").split(";")

			detected_files[f]["fullpath"] = normalize_path(self.downloadpath, f)
			detected_files[f]["version"] = f_version
			detected_files[f]["build"] = f_build
			detected_files[f]["filesize"] = os.path.getsize(detected_files[f]["fullpath"])
			detected_files[f]["modified"] = os.path.getmtime(detected_files[f]["fullpath"])
			detected_files[f]["osname"] = "iOS" if "iPhone" in f else "iPadOS"
			detected_files[f]["device_ids"] = []
			detected_files[f]["device_names"] = []

			for d in f_devices:
				if d not in self.device_names or d == "UniversalMac":
					# could be an iPhone/iPad with a different filename format
					# ex: iPad_Air_M2_26.4.1_23E254_Restore.ipsw
					#    ^iPad_Air_M2^
					# need to get any device that this identifier covers, the firmware should be the same
					groupid = "_".join(f_parts[:-3])

					if groupid in self.device_names:
						#gd_ids = self.device_names[groupid].split(";")[1:]

						#gd = gd_ids[0]

						if groupid not in detected_firmwares:
							detected_firmwares[groupid] = []
						detected_firmwares[groupid].append({
							"version": f_version,
							"filename": f
						})

						# get devices covered by this group
						devids = self.device_names[groupid].split(";")[1:]
						# if devids[0] not in detected_firmwares:
						# 	detected_firmwares[devids[0]] = []
						# detected_firmwares[devids[0]].append({
						# 	"version": f_version,
						# 	"filename": f
						# })
						for dev in devids:
							detected_files[f]["device_ids"].append(dev)

						detected_files[f]["device_names"].append(self.device_names[groupid].split(";")[0])
						detected_files[f]["device_group"] = groupid
					else:
						if f.startswith("._"):
							# no need to announce dotfiles
							continue
						term.print_error("* unrecognized firmware file: %s" % f)
					pass
				else:
					# d = device identifier like iPhone1,1
					detected_files[f]["device_ids"].append(d)
					detected_files[f]["device_names"].append(self.device_names[d])

					if d not in detected_firmwares:
						detected_firmwares[d] = []
					detected_firmwares[d].append({
						"version": f_version,
						"filename": f
					})

		return {
			"by_devid": detected_firmwares,
			"by_file": detected_files
		}

	# returns -1 if A is newer than B; 1 if B is newer than A; 0 if both version strings match
	# apparently python can just, naturally compare strings such as version numbers...? love it
	# but not always. they at least always have a major and minor version
	def compare_versions(a, b):
		a_ver = a.split(".")
		b_ver = b.split(".")

		if int(a_ver[0]) > int(b_ver[0]):
			return -1
		elif int(a_ver[0]) < int(b_ver[0]):
			return 1
		else:
			if int(a_ver[1]) > int(b_ver[1]):
				return -1
			elif int(a_ver[1]) < int(b_ver[1]):
				return 1
			else:
				# major and minor both match
				if len(a_ver) > len(b_ver):
					# a has a revision over b
					return -1
				elif len(a_ver) < len(b_ver):
					return 1
				else:
					# no change in length of version string
					# any revisions?
					if len(a_ver) <= 2:
						# no, so version match
						return 0

					# both a and b have revisions
					# TODO: consider case where an (a) revision may exist again
					if a_ver[2] > b_ver[2]:
						return -1
					elif a_ver[2] < b_ver[2]:
						return 1
					else:
						return 0
		# if a > b:
		# 	return -1
		# elif a < b:
		# 	return 1
		# else:
		# 	return 0

	def delete_old_firmwares_for(self, deviceid, *, dryrun=True):
		# first, get latest version
		latest = self.get_latest_firmware(deviceid)
		groupid = latest["groupid"]
		version = latest["version"]#.split(".")

		# find what's currently here
		firmwares = self.get_downloaded_ipswfiles()
		to_delete = []

		for ipsw in firmwares:
			if groupid in ipsw:
				ipswparts = ipsw.split("-")[-1].split("_")
				ipswversion = ipswparts[-3]

				if version > ipswversion:
					if ipsw not in to_delete:
						to_delete.append(ipsw)

		if dryrun:
			return to_delete

		pass

	# returns list of devices whose name best match the search terms
	def search_devices(self, searchfor):
		searchterms = searchfor.lower().strip().split(" ")

		best_matches = []
		best_score = 0

		for key in self.device_names:
			if "_" in key:
				# skip the multi-device match keys (iPad_Spring_2020, etc.)
				continue
			if "UniversalMac" in key:
				continue

			value = self.device_names[key]

			score = 0
			for t in searchterms:
				if t in value.lower().strip():
					score = score + 1

			if score > 0:
				if score == best_score:
					best_matches.append({"device": value, "id": key})

				if score > best_score:
					best_matches = [{"device": value, "id": key}]
					best_score = score

		return best_matches
	# end search_devices()

	def get_device_info(self, devid, *, ignore_cache=False):
		if devid in self.device_info_cache and not ignore_cache:
			return self.device_info_cache[devid]

		url = f'{self.baseurl}/device/{devid}'
		try:
			data = IPSW.read_from_url(url, 's')
			if "status" in data:
				if data["status"] == "404":
					return None

			self.device_info_cache[devid] = data
			return data
		except HTTPError as err:
			term.print_error("[IPSW.get_device_info()] " + str(err))
			term.print_warning("  for url: %s" % url)
			term.pause()
			return None

	def export_device_info(self, devid):
		data = self.get_device_info(devid)

		jsonobj = json.dumps(data, default=lambda o: o.__dict__, indent=4)

		outfile = os.path.join(self.downloadpath, devid + ".json")

		with open(outfile, "w") as f:
			f.write(jsonobj)

		term.print_msg("* wrote %s info to %s" % (devid, outfile))
	
	def get_all_firmwares(self, devid):
		dinfo = self.get_device_info(devid)
		if dinfo:
			for fw in dinfo["firmwares"]:
				ipswname = fw["url"].split("/")[-1]
				ipswname_parts = ipswname.split("_")
				groupid = "_".join(ipswname_parts[:-3])
				# convenience properties
				fw["saveto"] = os.path.join(self.downloadpath, ipswname)
				fw["groupid"] = groupid
				fw["ipswfilename"] = ipswname
				fw["prettysize"] = f"{round(fw["filesize"] / 1024 / 1024 / 1024, 2)} GB"
				fw["prettydate"] = datetime.strptime(fw["releasedate"] or fw["uploaddate"], "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%d")
			return dinfo["firmwares"]
		else:
			return None

	def get_signed_firmwares(self, devid):
		firmwares = self.get_all_firmwares(devid)
		if firmwares:
			resp = []

			for f in firmwares:
				if f["signed"]:
					resp.append(f)
			
			return resp
		else:
			return None

	def is_firmware_signed(self, devid, *, version=None, build=None):
		if not version and not build:
			raise ValueError("no value for either 'version' or 'build' was provided")

		firmwares = self.get_signed_firmwares(devid)
		if firmwares:
			for fw in firmwares:
				if not version:
					if fw["buildid"] == build and fw["signed"]:
						return True
				else:
					if fw["version"] == version and fw["signed"]:
						return True
		return False

	def get_firmware(self, devid, version="latest"):
		if version == "latest":
			return self.get_latest_firmware(devid)

		firmwares = self.get_all_firmwares(devid)
		for f in firmwares:
			if f["version"] == version:
				return f

		return None

	def get_latest_firmware(self, devid):
		firmwares = self.get_all_firmwares(devid)
		if firmwares:
			return firmwares[0]
		else:
			return None

	def download_firmware(self, url, filename, device, n, delete_old=False):
		ipswname = url.split("/")[-1]
		# filename = os.path.join(self.downloadpath, ipswname)

		# https://stackoverflow.com/questions/19724222/pycurl-attachments-and-progress-functions
		r = requests.head(url)
		total_size = int(r.headers.get('content-length', 0))
		block_size = 1024
		term.print_msg(f'Downloading from url: {url}')

		with tqdm(total=total_size, unit="iB", unit_scale=True, position=n, desc=ipswname) as pbar:
			total_dl_d = [0]
			def status(download_t, download_d, upload_t, upload_d, total=total_dl_d):
				pbar.update(download_d - total[0])
				total[0] = download_d

			with open(filename, "wb") as f:
				curl = pycurl.Curl()
				curl.setopt(pycurl.URL, url)
				curl.setopt(pycurl.WRITEDATA, f)
				curl.setopt(pycurl.FOLLOWLOCATION, True)
				curl.setopt(pycurl.NOPROGRESS, False)
				curl.setopt(pycurl.XFERINFOFUNCTION, status)
				curl.perform()

				curl.close()
		term.print_success("Completed: %s" % filename)

	def read_from_url(url, mode):
		try:
			r = urlopen(url)
		except HTTPError:
			raise
		else:
			if mode == 's': # string (str)
				data = r.read().decode('utf-8')
				return json.loads(data)
			elif mode == 'f': # file (bytes)
				data = r.read()
				return data
			else:
				term.print_error(f'Unknown mode given: {mode}')
				raise ValueError

class IPSWApp:
	dl_thread_count = 2

	# returns:
	# - {"device": [device name], "id": [device identifier]}
	# - None if no results are found for the search
	# - False if the action was cancelled
	@classmethod
	def menu_select_device(cls, ipsw):
		running = True

		while running:
			term.screen(f"{APP_NAME} ({APP_VERSION}) - Select device")

			searchfor = input("Enter all or part of an Apple device name: ").lower().strip()
			if searchfor == "":
				running = False
				return False

			results = ipsw.search_devices(searchfor)
			print()

			# results = [ {"device": [name], "id": [device id]}]
			if len(results) == 1:
				term.print_msg("Matched: %s" % (results[0]["device"]))
				if term.input_yn("Is this correct?"):
					running = False
					return results[0]
			elif len(results) == 0:
				term.print_warning("No results found for '%s'" % (searchfor))
				if not term.input_yn("Try again?"):
					running = False
					return None
			else:
				term.print_msg("Multiple results, please select:")
				for i in range(len(results)):
					print("%d. %s (%s)" % (i+1, results[i]["device"], results[i]["id"]))
				resp = term.input_int("> ", 1, within=[1, len(results)])

				running = True
				return results[resp-1]
		# end while
	# end menu_select_device()
	
	@classmethod
	def menu_select_firmware(cls, ipsw, device, show_all=False, *, allow_ctrlc=False):
		try:
			term.screen(f"{APP_NAME} ({APP_VERSION}) - Select firmware for: {device["device"]}")

			term.print_msg("Fetching firmware list for %s (%s)..." % (device["device"], device["id"]))

			firmwares = ipsw.get_signed_firmwares(device["id"]) if not show_all else ipsw.get_all_firmwares(device["id"])

			if not firmwares:
				term.print_warning("No firmwares found; please try another search")
				term.pause()
				return None

			for i in range(len(firmwares)):
				if i < 9 and len(firmwares) >= 10:
					print(" ", end="")
				print("%d. %s%s (%s)%s" % (i+1, term.fgcolors["green"] if firmwares[i]["signed"] else term.fgcolors["yellow"], firmwares[i]["version"], firmwares[i]["buildid"], term._reset()))

				print("    %.2f GB / Released on %s" % (round(firmwares[i]["filesize"] / 1024 / 1024 / 1024, 2), datetime.strptime(firmwares[i]["releasedate"], "%Y-%m-%dT%H:%M:%SZ").strftime("%Y-%m-%d")))
				print
			if not show_all:
				print("%d. Show all firmwares (including unsigned)" % (len(firmwares) + 1))
			print()
			resp = term.input_int("Select a firmware to download:", 1, within=[1, len(firmwares) + (1 if not show_all else 0)], allow_ctrlc=allow_ctrlc)
			if not resp:
				return None

			if not show_all and resp == (len(firmwares) + 1):
				return cls.menu_select_firmware(ipsw, device, True)
			else:
				return firmwares[resp-1]
		except Exception as e:
			term.print_error(str(e))
			term.pause()
			# raise e
	# end menu_select_firmware()

	@classmethod
	def menu_select_multiple_devices(cls, ipsw, *, latest=True):
		devices = []

		selection_done = False
		last_result = None

		while not selection_done:
			term.screen(f"{APP_NAME} ({APP_VERSION}) - Select devices")

			if last_result:
				if last_result["type"] == "error":
					term.print_error(last_result["text"])
				elif last_result["type"] == "success":
					term.print_success(last_result["text"])
				elif last_result["type"] == "warning":
					term.print_warning(last_result["text"])
				else:
					term.print_msg(last_result["text"])

				if "details" in last_result:
					term.print_msg(last_result["details"])

				print()

				last_result = None

			term.print_msg("Current selection:")
			for i in range(len(devices)):
				print("  %d. %s (%s) - %s" % (i+1, devices[i]["device"], devices[i]["id"], devices[i]["fw_wants"]))

			print("a. Add a device")
			print("x. Clear selection")
			print("c. Confirm selection")
			print("q. Cancel")
			print()
			print("Select a number from above to remove that device from the selection.")
			print()
			resp = term.input("> ", "a" if len(devices) == 0 else "c").lower().strip()

			if resp == "a":
				d = cls.menu_select_device(ipsw)
				if d:
					if not latest:
						fw = cls.menu_select_firmware(ipsw, d)
						if fw is not False:
							d["fw_wants"] = fw["version"]
							devices.append(d)
					else:
						d["fw_wants"] = "latest"
						devices.append(d)
			elif resp == "x":
				devices = []
			elif resp == "c":
				selection_done = True
			elif resp == "q":
				devices = False
				selection_done = True
			else:
				i = term.util.parse_int(resp, defaultvalue=-90)

				if i not in [1, len(devices)]:
					last_result = {"type": "error", "text": "Invalid option selected"}
				else:
					last_result = {"type": "message", "text": f"Removed '{devices[i-1]["device"]}'"}
					del devices[i-1]

		return devices
	# end menu_select_multiple_devices()
	
	# delete_list => list of filenames to delete
	# if None, deletes old versions for each IPSW/devID found in folder
	@classmethod
	def action_download_multiple(cls, ipsw, devices, *, delete_old_versions=False, blocking=True, delete_list=None):
		term.screen(f"{APP_NAME} ({APP_VERSION}) - Confirm downloads")

		term.print_msg("The following firmwares will be downloaded:")
		target_files = []

		for d in devices:
			osname = "iOS"
			if "Mac" in d["device"]:
				osname = "macOS"
			elif "iPad" in d["device"]:
				osname = "iPadOS"

			print(f"- {d["device"]} ({d["id"]}) - {osname} {d["firmware"]["version"]} ({d["firmware"]["buildid"]})")
			term.print_warning(f"  Size: {d["firmware"]["prettysize"]} / Release date: {d["firmware"]["prettydate"]}")
			term.print_msg(f"  -> {d["firmware"]["saveto"]}")
			target_files.append(d["firmware"]["saveto"])

		ipsw_files_to_remove = []

		if delete_old_versions:
			print()
			term.print_warning("The following firmwares will be DELETED:")
			# device => list of: {"version": version-string, "filename": full-path-to-ipsw}
			# ipsw_files = ipsw.get_downloaded_firmwares()
			# for dev in ipsw_files:
			if delete_list:
				ipsw_files_to_remove = [f for f in delete_list]
			else:
				for dev in devices:
					if delete_list is None or dev["id"] in delete_list:
						older = ipsw.delete_old_firmwares_for(dev["id"], dryrun=True)
						for o in older:
							ipsw_files_to_remove.append(o)

			# remove any that already exist from the deletion queue
			# TODO: get firmwares downloaded for device
			# remove all but the latest
			# - if the latest one exists, keep it
			# - if the latest one doesn't exist on disk, delete all previous and then download the new one
			# 
			# for f in ipsw_files:
			# 	f_path = os.path.join(ipsw.downloadpath, f)
			# 	for d in devices:
			# 		dl_path = d["firmware"]["saveto"]
			# 		if not os.path.exists(dl_path):

			
			for i in ipsw_files_to_remove:
				print("- %s" % i)

		print()

		if len(target_files) == 0 and len(ipsw_files_to_remove) == 0:
			term.print_msg("Nothing to do here!")
			term.pause()

			return True

		if term.input_yn("Is this correct?"):
			if delete_old_versions:
				for i in ipsw_files_to_remove:
					ipath = os.path.join(ipsw.downloadpath, i)
					os.remove(ipath)

			remaining = len(devices)

			term.print_msg("Beginning download, this could take a while...")

			n = 0
			with ThreadPoolExecutor(max_workers=cls.dl_thread_count) as executor:
				futures = []
				for d in devices:
					url = d["firmware"]["url"]
					filename = d["firmware"]["saveto"]
					future = executor.submit(ipsw.download_firmware, url, filename, d, n, delete_old_versions)
					futures.append(future)
					n += 1

				for future in as_completed(futures):
					try:
						future.result()
						remaining = remaining - 1
						n -= 1
					except Exception as e:
						term.print_error(f"{e}")
						remaining -= 1

			spinner = ["\b|", "\b/", "\b-", "\b\\"]
			s = 0
			while remaining > 0 and blocking:
				print("%s" % spinner[s], end="")
				time.sleep(0.5)
				s = s + 1
				if s >= len(spinner):
					s = 0
			print()
			print()
			term.print_success("All done!")
			print()
			term.pause()
			return True
		else:
			return False

	@classmethod
	def action_update_existing_files(cls, ipsw, *, custom_selection=False):
		term.screen(f"{APP_NAME} ({APP_VERSION}) - Existing firmwares")
		term.print_msg("Firmwares in %s:" % ipsw.downloadpath)
		print()

		existing_firmwares = ipsw.get_downloaded_firmwares_dict()["by_file"]
		if len(existing_firmwares) == 0:
			term.print_error("* No firmwares detected!")
			term.pause()
			return

		to_be_downloaded = []
		to_be_deleted = []

		# filename/hashes of files to download, to avoid pulling or prompting for duplicate files
		target_files = []
		
		for filename in existing_firmwares:
			firmware = existing_firmwares[filename]
			# term.print(filename, color="black", bgcolor="gray")
			term.print_msg(filename)

			# existing_firmwares[filename] = something like
			# filename = iPad_Fall_2022_26.5_23F77_Restore.ipsw
			# existing_firmwares[filename] => "iPad_Fall_2022_26.5_23F77_Restore.ipsw": {
	        #     "fullpath": "D:/iOS Firmware/iPad_Fall_2022_26.5_23F77_Restore.ipsw",
	        #     "version": "26.5",
	        #     "build": "23F77",
	        #     "filesize": 9817475201,
	        #     "modified": 1778622259.82,
	        #     "osname": "iPadOS",
	        #     "device_ids": [
	        #         "iPad13,18",
	        #         "iPad13,19"
	        #     ],
	        #     "device_names": [
	        #         "iPad (10th gen)"
	        #     ],
	        #     "device_group": "iPad_Fall_2022"
	        # }
			if len(firmware["device_names"]) > 1 and "UniversalMac" not in firmware["device_names"]:
				# same firmware file name for multiple device models
				# some of which may allow a newer version
				# 
				# need to check for updates for each dev-id
				# 
				# with UniversalMac group... this might be a problem lol
				pass

			# device_id = firmware["device_ids"][0]
			# device_name = firmware["device_names"][0]
			# latest = ipsw.get_latest_firmware(device_id)

			if "UniversalMac" in firmware["device_names"]:
				term.print_warning("* macOS firmware updating not implemented yet")
			else:
				groupid = None
				if "device_group" in firmware:
					groupid = firmware["device_group"]

				skip_group = False
				for devid in firmware["device_ids"]:
					if skip_group:
						continue

					has_latest = False
					has_signed = False # if the currently-downloaded firmware is still signed by apple

					latest = ipsw.get_latest_firmware(devid)
					target = latest["ipswfilename"] + ":" + latest["sha256sum"]
					
					if target in target_files:
						print("    * %s uses same firmware" % ipsw.device_names[devid])
						continue

					term.print("  - %s (%s)%s" % (ipsw.device_names[devid], devid, (f" - {groupid}" if groupid else "")))
					# print("  - %s" % devid)

					if IPSW.compare_versions(latest["version"] , firmware["version"]) == 0:
						# both versions are the same
						has_latest = True
						term.print_success("    latest: %s / current: %s" % (latest["version"] , firmware["version"]))
					elif IPSW.compare_versions(latest["version"], firmware["version"]) > 0:
						# current firmware is newer; can happen with devices under the same filename grouping, but with different update cutoffs
						# skip and continue
						continue
					else:
						term.print_warning("    latest: %s / current: %s" % (latest["version"] , firmware["version"]))

					if ipsw.is_firmware_signed(devid, version=firmware["version"]):
						has_signed = True

					# at this point we know, for this deviceid, if
					# - the current firmware is signed
					# - the current firmware is latest
					if not has_latest:
						if custom_selection:
							# prompt for actions
							if term.input_yn("Download newer firmware?"):
								to_be_downloaded.append({
									"device": ipsw.device_names[devid],
									"id": devid,
									"firmware": latest
								})

								# remember this download target
								print("    * download target: %s" % target)
								target_files.append(target)

								if term.input_yn("Delete existing firmware?"):
									to_be_deleted.append(filename)
							else:
								skip_group = True
						else:
							# download if newer, and delete older
							to_be_downloaded.append({
								"device": ipsw.device_names[devid],
								"id": devid,
								"firmware": latest
							})
							to_be_deleted.append(filename)

							# remember this download target
							print("    * download target: %s" % target)
							target_files.append(target)
					else:
						term.print_success("    * Already have latest version")
		# end for (each ipsw file)
		
		if not custom_selection:
			if term.input_yn("Customize this selection?", False):
				cls.action_update_existing_files(ipsw, custom_selection=True)
			else:
				if not term.input_yn("Delete older firmwares?"):
					to_be_deleted.clear()
				cls.action_download_multiple(ipsw, to_be_downloaded, delete_old_versions=True, delete_list=to_be_deleted)

		if custom_selection:
			cls.action_download_multiple(ipsw, to_be_downloaded, delete_old_versions=True, delete_list=to_be_deleted)
	# end action_update_existing_files()

	@classmethod
	def action_update_existing(cls, ipsw, *, custom=False):
		cls.action_update_existing_files(ipsw, custom_selection=custom)
		return

		term.screen(f"{APP_NAME} ({APP_VERSION}) - Existing firmwares")
		term.print_msg("Firmwares in %s:" % ipsw.downloadpath)

		existing_firmwares = ipsw.get_downloaded_firmwares()
		# existing_firmwares = ipsw.get_downloaded_firmwares_dict()["by_file"] #_dict()["by_devid"]

		# print(existing_firmwares)
		# term.pause()
		if len(existing_firmwares) == 0:
			term.print_error("* No firmwares detected!")
			term.pause()
			return

		to_be_downloaded = []
		to_be_deleted = None if not custom else []
		seen_files = []

		# e_f = [deviceid or groupid] => [list, of, versions, found]
		for deviceid in existing_firmwares:
			# print(deviceid)
			print("- %s" % (ipsw.device_names[deviceid].split(";")[0] if ";" in ipsw.device_names[deviceid] else ipsw.device_names[deviceid]))

			# get a singular device identifier
			did = deviceid
			devkey = ipsw.get_group_for_deviceid(did)
			if did_tmp := ipsw.get_devices_from_groupid(deviceid):
				did = did_tmp[0]
				# print(did)
				# print(devkey)
				# term.pause()
			elif ";" in ipsw.device_names[deviceid]:
				gid = deviceid
				gdevices = ipsw.device_names[gid].split(";")[1:]
				did = gdevices[0]
				devkey = gid

			if devkey in seen_files:
				continue

			# get the latest firmware version for this device
			latest = ipsw.get_latest_firmware(did)
			has_latest = False
			has_signed = False # if the currently-downloaded firmware is still signed by apple

			existing = []
			for firmware_version in existing_firmwares[deviceid]:
				# print(firmware_version)
				existing.append("%s %s" % ("iPadOS" if "iPad" in deviceid else "iOS", firmware_version))
				if latest["version"] == firmware_version:
					has_latest = True
				if ipsw.is_firmware_signed(did, version=firmware_version):
					has_signed = True
			if has_latest:
				print("  Existing: %s%s" % (", ".join(existing), " (unsigned)" if not has_signed else ""))
			else:
				term.print_warning("  Existing: %s%s" % (", ".join(existing), " (unsigned)" if not has_signed else ""))
			term.print_success("  Latest: %s (%s)" % (latest["version"], latest["buildid"]))

			if not has_latest:
				this_download = True
				this_delete = True
				if custom:
					if this_download := term.input_yn("Download updated firmware?"):
						this_delete = term.input_yn("Delete existing firmware?")

				if this_download:
					job = {
						"device": ipsw.device_names[did],
						"id": did,
						"firmware": latest
					}
					seen_files.append(devkey)
					to_be_downloaded.append(job)

				if custom and this_delete:
					to_be_deleted.append(did)
		# end for
		
		print()
		if not custom:
			customize = term.input_yn("Do you want to customize this selection?", False)
			if customize:
				cls.action_update_existing(ipsw, custom=True)
				return
		
		clear_existing = True
		if not custom:
			clear_existing = term.input_yn("Do you want to delete older firmwares?")

		cls.action_download_multiple(ipsw, to_be_downloaded, delete_old_versions=clear_existing, delete_list=to_be_deleted)
	# end action_update_existing()

	@classmethod
	def action_clear_downloads(cls, ipsw):
		ipswfiles = ipsw.get_downloaded_ipswfiles(fullpaths=True)
		if term.modalalert("Confirm delete", "There are %d firmware file(s) in the following folder:\n\n%s\n\nThey will ALL be deleted.  Are you sure about that?" % (len(ipswfiles), ipsw.get_path()), [{"label": "Yes", "value": True, "activebg": "red"}, {"label": "No", "value": False}], background_color="black", default_button=1):
			# delete the files
			print(ipswfiles)
			term.print_warning("** not implemented yet...")
			term.pause()

	@classmethod
	def action_manage_downloads(cls, ipsw):
		term.screen(f"{APP_NAME} ({APP_VERSION}) - Manage firmwares")
		term.print_msg("Detecting firmwares in %s:" % ipsw.downloadpath)

		resp = ipsw.get_downloaded_firmwares_dict()
		existing_files = resp["by_file"]

		if len(existing_files) == 0:
			term.print_error("* No firmwares detected!")
			term.pause()
			return

		# fields: fullpath, version, filesize (in bytes), modified (file mod time), device_ids (list of compatible device ids), osname (iOS or iPadOS), device_names
		# for ipswfile in existing_files:
		# 	f = existing_files[ipswfile]

		# 	term.print_msg(ipswfile)
		# 	print(f)
	
		def _fmap_to_choices(fmap):
			resp = []
			for f in fmap:
				ifile = fmap[f]
				resp.append({
					"file": f,
					"fullpath": ifile["fullpath"],
					"version": ifile["version"],
					"build": ifile["build"],
					"filesize": ifile["filesize"],
					"modified": ifile["modified"],
					"osname": ifile["osname"],
					"device_ids": ifile["device_ids"],
					"device_names": ifile["device_names"]
				})
			return resp

		running = True
		do_refresh = False

		main_help = [
			"Press UP/DOWN to change selection, and ENTER to select",
			"Press R to refresh the list",
			"Press S for a summary of the highlighted firmware",
			"Press U to check for a newer version of the highlighted firmware",
			"Press D to delete the highlighted firmware",
			"Press X to quit",
			"===================================="
		]

		hotkeys_map = {
			"r": "refresh",
			"s": "summary",
			"q": "back",
			"u": "update",
			"d": "delete",
			"h": "hash"
		}

		def print_option(index: int, highlighted: bool, opt: object):
			format_str = "%s - %s %s (%s)"
			values = (
				f"{opt["device_names"][0]}..." if len(opt["device_names"]) > 1 else opt["device_names"][0],
				opt["osname"],
				opt["version"],
				opt["device_ids"][0]
			)

			if highlighted:
				term.print_highlighted("  " + (format_str % values))
			else:
				term.print("  " + (format_str % values))

		def summarize(firmware):
			# SUMMARY
			term.screen(f"{APP_NAME} - {firmware["file"]}")
			term.print_msg(f"{firmware["osname"]} {firmware["version"]}")
			term.print("  for: " + ", ".join(firmware["device_names"]))
			print()
			print("  Version: " + firmware["version"] + " (" + firmware["build"] + ")")
			print("  Path: " + firmware["fullpath"])
			print("  Size: " + f"{round(firmware["filesize"] / 1024 / 1024 / 1024, 2)} GB")
			print()
			print("  Compatibile with: ")
			for did in firmware["device_ids"]:
				print(f"  - {ipsw.device_names[did]} ({did})")
				if ipsw.is_firmware_signed(did, version=firmware["version"]):
					term.print_success("    * Signed by Apple - you can restore this device to this firmware")
				else:
					term.print_warning("    * NOT signed by Apple - you can NOT restore this device to this firmware")
			# print(json.dumps(firmware, indent=4))

			print()
			term.pause()

		initial_index = 0
		while running:
			if do_refresh:
				term.screen(f"{APP_NAME} ({APP_VERSION}) - Manage firmwares")
				term.print_msg("Detecting firmwares in %s:" % ipsw.downloadpath)

				existing_files = ipsw.get_downloaded_firmwares_dict()["by_file"]
				if len(existing_files) == 0:
					term.print_error("* No firmwares detected!")
					term.pause()
					running = False
					continue

				do_refresh = False

			choices = _fmap_to_choices(existing_files)

			selection = term.menu("Showing firmwares in: " + ipsw.get_path(), choices, title="Firmware Manager", format_str="%s", format_fields=["file"], on_print_option=print_option, initial_index=initial_index, return_index=True, instructions=main_help, hotkeys=hotkeys_map, quit_keys=["x", "backspace", "esc"])

			if selection is None:
				# cancelled
				running = False
			else:
				if type(selection) is tuple:
					index, cmd, key = selection
					firmware = choices[index]

					if key == "r":
						# REFRESH
						do_refresh = True
					elif key == "s":
						summarize(firmware)
					elif key == "q":
						running = False
					elif key == "h":
						term.screen(f"{APP_NAME} - {firmware["file"]}")
						term.print_msg(f"{firmware["osname"]} {firmware["version"]}")
						print("  for: " + ", ".join(firmware["device_names"]))
						print()
						print("  Version: " + firmware["version"] + " (" + firmware["build"] + ")")
						print("  Path: " + firmware["fullpath"])
						print("  Size: " + f"{round(firmware["filesize"] / 1024 / 1024 / 1024, 2)} GB")
						print()
						print("  Calculating SHA256 checksum, please wait...")
						print("  sha256: %s" % get_sha256(firmware["fullpath"]))
						print()
						term.pause()
					elif key == "u":
						# UPDATE SELECTED
						c_version = firmware["version"]
						c_build = firmware["build"]

						devid = firmware["device_ids"][0]
						a_firm = ipsw.get_latest_firmware(devid)
						
						term.print(firmware["fullpath"])
						term.print_msg("Downloaded version: ", end="")
						term.print(f"{c_version} ({c_build})")

						term.print_msg("Latest version: ", end="")
						# print(a_firm["version"])
						match IPSW.compare_versions(c_version, a_firm["version"]):
							case -1:
								term.print_success(a_firm["version"] + " (" + a_firm["buildid"] + ")")
								print()
								term.print_success("You have the latest (newer?) version already.  No action is needed.")
								term.pause()
							case 0:
								term.print_success(a_firm["version"] + " (" + a_firm["buildid"] + ")")
								print()
								term.print_success("You have the latest version already.  No action is needed.")
								term.pause()
							case 1:
								term.print_warning(a_firm["version"] + "(" + a_firm["buildid"] + ")")
								print("  Size: " + a_firm["prettysize"])
								print("  Date: " + a_firm["prettydate"])
								print()
								term.print_msg("A newer version of this firmware is available.")
								if term.input_yn("Download now?"):
									ipsw.download_firmware(a_firm["url"], a_firm["saveto"], a_firm["identifier"], 0)
					elif key == "d":
						# DELETE SELECTED
						print("Confirm delete file: ")
						term.print_msg(f"  {firmware["osname"]} {firmware["version"]}")
						print("    for: " + ", ".join(firmware["device_names"]))
						print()
						print("    Version: " + firmware["version"] + " (" + firmware["build"] + ")")
						print("    Path: " + firmware["fullpath"])
						print("    Size: " + f"{round(firmware["filesize"] / 1024 / 1024 / 1024, 2)} GB")
						print()
						if term.input_yn("Really delete this file?", False):
							try:
								os.remove(firmware["fullpath"])
							except Exception as ex:
								term.print_error(ex)
								term.pause()

							do_refresh = True
				elif type(selection) is int:
					# firmware was selected; index was returned
					firmware = choices[selection]
					initial_index = selection
					summarize(firmware)
					# 
					# fields: file, fullpath, version, filesize (in bytes), modified (file mod time), device_ids (list of compatible device ids), osname (iOS or iPadOS), device_names


	@classmethod
	def main(cls, *, ipsw=None):
		if ipsw is None:
			ipsw = IPSW()
		# print(ipsw.device_names)

		running = True
		last_result = None

		try:
			while running:
				term.screen(f"{APP_NAME} - {APP_VERSION} ({APP_DATE})")

				if last_result:
					if last_result["type"] == "error":
						term.print_error(last_result["text"])
					elif last_result["type"] == "success":
						term.print_success(last_result["text"])
					elif last_result["type"] == "warning":
						term.print_warning(last_result["text"])
					else:
						term.print_msg(last_result["text"])

					if "details" in last_result:
						term.print_msg(last_result["details"])

					print()

					last_result = None

				term.print_msg("")
				term.print_msg("Choose a menu option:")
				print("1. Download latest firmware for device(s)")
				print("2. Select firmwares for device(s)")
				print("3. Set download location [type 'dir' to manually specify]")
				print("4. Update all firmwares in folder")
				print("5. Manage downloaded firmwares")
				print("6. Clear ALL downloaded firmwares")
				print("7. Set download location to iTunes appdata")
				print("8. Set max simultaneous downloads [current: %d]" % cls.dl_thread_count)
				print("x. Exit")
				print()
				resp = term.input(">", "1").lower()

				if resp in ["1", "2"]:
					devices = cls.menu_select_multiple_devices(ipsw, latest=(resp == "1"))

					if devices is not False:
						if len(devices) == 0:
							last_result = {"type": "error", "text": "** No devices selected!"}
							continue

						term.print_msg("Fetching firmware list...")

						for d in devices:
							d["firmware"] = ipsw.get_firmware(d["id"], d["fw_wants"])

						print(devices)
						# term.pause()

						result = cls.action_download_multiple(ipsw, devices)
					else:
						last_result = {"type": "error", "text": "** Cancelled by user"}
				elif resp == "3":
					print()

					old_path = ipsw.downloadpath
					# new_path = term.input("Enter new firmware path: ", old_path)
					new_path = term.filebrowser("Select firmware directory", "Choose the folder containing your .ipsw firmware files", start_dir=old_path, allow_mkdir=True, folder_select=True, show_files_in_folder_select=True)
					if not new_path:
						continue

					# validate
					if not os.path.exists(new_path):
						if term.input_yn("This path doesn't exist -- create it?"):
							os.makedirs(new_path, exist_ok=True)
						else:
							continue

					ipsw.set_path(new_path)

					last_result = {"type": "success", "text": "* Download path: %s" % ipsw.downloadpath}
				elif resp == "dir":
					print()

					old_path = ipsw.downloadpath
					new_path = term.input("Enter new firmware path: ", old_path)

					# validate
					if not os.path.exists(new_path):
						if term.input_yn("This path doesn't exist -- create it?"):
							os.makedirs(new_path, exist_ok=True)
						else:
							continue

					ipsw.set_path(new_path)

					last_result = {"type": "success", "text": "* Download path: %s" % ipsw.downloadpath}
				elif resp == "4":
					# scan download path for firmware files; identify what devices they are for
					# get latest firmware for those devices
					# prompt user to keep or delete previous firmware files
					# if delete: only delete the old file when the new one has completed
					resp = cls.action_update_existing(ipsw)
				elif resp == "5":
					cls.action_manage_downloads(ipsw)
				elif resp == "6":
					cls.action_clear_downloads(ipsw)
				elif resp == "7":
					ipsw.use_itunes_path()
					last_result = {"type": "success", "text": "* Download path: %s" % ipsw.downloadpath}
				elif resp == "8":
					threadcount = term.input_int("Enter number of simultaneous downloads:", cls.dl_thread_count, within=[1, 6], allow_ctrlc=True, cancelvalue=2)

					cls.dl_thread_count = threadcount

					last_result = {"type": "success", "text": "* Simultaneous download count changed to: %d" % cls.dl_thread_count}
				elif resp == "x":
					running = False
				else:
					pass
			# end while
			
			term.clear()
		except KeyboardInterrupt as kint:
			print()
			print()
			term.print_error("** Aborted by user")
		except Exception as e:
			raise e
	# end main()

if __name__ == "__main__":
	IPSWApp.main()