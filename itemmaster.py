import csv, json, math, os, shutil, sys, tempfile, time, webbrowser
from json.decoder import JSONDecodeError
from datetime import timedelta
from datetime import datetime

from jb93term import Terminal as term
default_path = os.getcwd() # os.path.realpath(__file__)

def remap_dict(d, maps):
	for m in maps:
		#print(m)
		if m[0] in d:
			#print("d['%s'] = '%s'" % (m[0], d[m[0]]))
			d[m[1]] = d[m[0]]
			del d[m[0]]
	return d

class Itemmasters:
	# map SF internal field name to human-readable (Salesforce Data Loader desktop app uses the internal field names, dataloader.io uses the pretty names)
	itemmaster_remaps = [
		("akatia__Auto_Merge__c", "Auto Merge"),
		("Billing_Name__c", "Billing Name"),
		("akatia__Commodity_Group_Code__c", "Commodity Group Code"),
		("akatia__SubGroup_Code__c", "Commodity Sub-Group Code"),
		("akatia__Description__c", "Description"),
		("akatia__DNGenerate_Putaway__c", "Do not Generate Putaway?"),
		("akatia__Serial_Mgt__c", "is Serial # Controlled"),
		("Name", "Item #"),
		("akatia__Item__c", "UPC #")
	]

	inst = None
	def getinst():
		if Itemmasters.inst is None:
			Itemmasters.inst = Itemmasters()
		return Itemmasters.inst

	def get_db_filename():
		return os.path.join(default_path, "itemmasters.csv")
	def get_db_mtime():
		file = Itemmasters.get_db_filename()
		timestamp = os.path.getmtime(file)
		dt = datetime.fromtimestamp(timestamp)

		return dt.strftime("%Y-%m-%d %I:%M %p")

	def __init__(self, *, filename=None):
		self.masters = {}
		self.filename = filename
		if not self.filename:
			self.filename = Itemmasters.get_db_filename()
		self.load()

	# fields to keep: Billing Name, Description, Is Serial # Controlled, Item # (primary key), UPC #
	# works 5-19-2026
	def load(self):
		self.masters = {}

		with open(self.filename, "r") as csvfile:
			csvreader = csv.DictReader(csvfile)
			for row in csvreader:
				# print(row)
				row = remap_dict(row, Itemmasters.itemmaster_remaps)
				# print(row)
				im = {
					"description": row["Description"],
					"itemnumber": row["Item #"],
					"serialized": (row["is Serial # Controlled"].upper() == "TRUE"),
					"upc": row["UPC #"],
					"type": row["Commodity Group Code"],
					"subtype": row["Commodity Sub-Group Code"],
					"is_device": row["Commodity Group Code"] == "Asset",
					"is_apple_device": row["Commodity Group Code"] == "Asset" and "apple" in row["Commodity Sub-Group Code"].lower(),
					"is_phone": row["Commodity Group Code"] == "Asset" and "phone" in row["Commodity Sub-Group Code"].lower(),
					"is_aircard": row["Commodity Group Code"] == "Asset" and "aircard" in row["Commodity Sub-Group Code"].lower()
				}
				self.masters[row["Item #"]] = im
				# print(im)

	def get(self, itemnumber):
		if itemnumber in self.masters:
			return self.masters[itemnumber]
		return None

	def lookup_by_upc(self, upc, *, search_devices=True, search_items=False):
		for m in self.masters:
			# if not search_devices:
			# 	if self.masters[m]["is_device"]:
			# 		continue
			# if not search_items:
			# 	if not self.masters[m]["is_device"]:
			# 		continue

			if upc == self.masters[m]["upc"]:
				return self.masters[m]
		return None

	# search Item # and Description fields
	# return best matches
	# 
	# if input is numeric, a UPC search is performed
	def lookup(self, searchfor, *, show_devices=True, show_items=False, favor_code_matches=True):
		best_matches = []
		best_score = 0

		searchterms = searchfor.lower().strip().split(" ")

		for m in self.masters:
			if not show_devices:
				if self.masters[m]["is_device"]:
					continue
			if not show_items:
				if not self.masters[m]["is_device"]:
					continue

			score = 0

			for t in searchterms:
				if m.lower() == t:
					score += 10

				if favor_code_matches:
					if t in m.lower():
						score += 5

					if t in self.masters[m]["description"].lower():
						score += 2
				else:
					if t in m.lower() or t in self.masters[m]["description"].lower():
						score += 2

				# if self.masters[m]["serialized"]:
				# 	score *= 2

			if score > 0:
				if score == best_score:
					best_matches.append(self.masters[m])

				if score > best_score:
					best_matches = [self.masters[m]]
					best_score = score

		return best_matches

	def select(self, searchfor, *, search_devices=True, search_items=False, favor_code_matches=True):
		if searchfor.isnumeric():
			master = self.lookup_by_upc(searchfor, search_devices=search_devices, search_items=search_items)
			if master:
				term.print_msg("    Selected: %s (%s)" % (master["itemnumber"], master["description"]))
				return master
			else:
				term.print_warning("    * No matches for %s" % searchfor)
				return None

		results = self.lookup(searchfor, show_devices=search_devices, show_items=search_items, favor_code_matches=favor_code_matches)
		options = []
		for r in results:
			options.append(f"{r['itemnumber']} ({r['description']})")

		if len(results) == 1:
			# one best result
			term.print_msg("    Matched: %s" % results[0]["description"])
			return results[0]
		elif len(results) == 0:
			# no results
			term.print_warning("    * No matches for %s" % searchfor)
			return None
		else:
			# multiple results
			# need to prompt for the correct option
			term.print_msg("    Multiple results found, please choose:")
			options.append("New search")
			options.append("Enter Itemmaster name")
			for i in range(len(options)):
				print("     %d. %s" % (i+1, options[i]))
			resp = term.input_int("    Select a product: ", 1, within=[1, len(options)], allow_ctrlc=True)

			if not resp:
				return None

			if resp == len(options): # Enter device name
				tempim = term.input("  Enter Itemmaster name: ", self.product).strip()
				if tempim in self.masters:
					return self.masters[tempim]
				else:
					return None
			elif resp == len(options) - 1: # new search
				pass
				return False
			else:
				term.print_msg("    Selected: %s (%s)" % (results[resp-1]["itemnumber"], results[resp-1]["description"]))
				return results[resp-1]