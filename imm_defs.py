# imm_defs.py
# contains various data structures used by iMobileManager
# 
# credit to Hao for most of these

import enum, importlib.util, json, logging, os, platform, re, shutil, subprocess, sys, tarfile, tempfile, textwrap, webbrowser, zipfile
from base64 import b64decode
from collections import defaultdict, namedtuple
from collections.abc import Collection, Container, Iterator, Mapping, MutableMapping, MutableSet, Set
from datetime import date, datetime, timezone, timedelta
from hashlib import file_digest
from itertools import batched, chain, zip_longest
from concurrent.futures import Executor, ThreadPoolExecutor
from time import sleep, strftime, time
from types import TracebackType
from typing import cast, Any
from urllib.request import urlretrieve
from xml.etree import ElementTree

# https://mcc-mnc.net/mcc-mnc.xlsx
from jb93term import Terminal as term

class REPLCompleter():
	__HAS_READLINE = importlib.util.find_spec("readline") is not None

	################################################################################
	# Magic Methods
	################################################################################

	def __enter__(self) -> None:
		if self.__HAS_READLINE:
			import rlcompleter, readline
			readline.parse_and_bind("tab: complete") # type: ignore

	def __exit__(self, exception_type: builtins.type[BaseException] | None, exception: BaseException | None, traceback: TracebackType | None) -> None:
		if self.__HAS_READLINE:
			import readline
			readline.parse_and_bind("tab: self-insert") # type: ignore

	@classmethod
	def has_dependencies_installed(cls):
		return cls.__HAS_READLINE

class Carrier(enum.Enum):
	#COMCAST = ("Xfinity Mobile", frozenset((311480, 310004, 310005, 310006, 310010, 310012, 310013, 310350, 310590, 310820, 310890, 310910, 311012, 311110, 311270, 311271, 311272, 311273, 311274, 311275, 311276, 311277, 311278, 311279, 311280, 311281, 311282, 311283, 311284, 311285, 311286, 311287, 311288, 311289, 311390, 311481, 311482, 311483, 311484, 311485, 311486, 311487, 311488, 311489, 311590, 312770)))
	VERIZON = ("Verizon", frozenset((311480, 310004, 310005, 310006, 310010, 310012, 310013, 310350, 310590, 310820, 310890, 310910, 311012, 311110, 311270, 311271, 311272, 311273, 311274, 311275, 311276, 311277, 311278, 311279, 311280, 311281, 311282, 311283, 311284, 311285, 311286, 311287, 311288, 311289, 311390, 311481, 311482, 311483, 311484, 311485, 311486, 311487, 311488, 311489, 311590, 312770)))
	ATT = ("AT&T", frozenset((310410, 310016, 310030, 310070, 310080, 310090, 310150, 310170, 310280, 310560, 310670, 310680, 310950, 311070, 311090, 311180, 311190, 312090, 312670, 312680, 313210)))
	TMOBILE = ("T-Mobile", frozenset((310260, 310660, 310200, 310210, 310220, 310230, 310240, 310250, 310270, 310310, 310490, 310800, 310160)))

	################################################################################
	# Factory Methods
	################################################################################

	@classmethod
	# @lru_cache(1 << 9)
	def from_bundle(cls, bundle: str) -> Self | None:
		return next((carrier for carrier in cls if carrier.name.lower() in bundle.lower()), None)

	@classmethod
	# @lru_cache(1 << 9)
	def from_imsi(cls, imsi: str) -> Self | None:
		return next((carrier for carrier in cls if int(imsi[:6]) in carrier.value[1]), None)

	################################################################################
	# Magic Methods
	################################################################################

	# @override
	def __str__(self) -> str:
		return self.value[0]

# https://github.com/samdmarshall/iOS-Internals/blob/master/lockbot/lockbot/lockdown_keys.h
class Attribute(enum.EnumDict):
	class Domain(enum.EnumDict):
		_ = "$" # Separator
		PREFIX = "com.apple."
		BATTERY = PREFIX + "mobile.battery"
		DISK_USAGE = PREFIX + "disk_usage"
		FACTORY_DISK_USAGE = PREFIX + "disk_usage.factory"
		INTERNAL = PREFIX + "mobile.internal"
		RESTRICTIONS = PREFIX + "mobile.restriction"
		USER_PREFERENCES = PREFIX + "mobile.user_preferences"
		SOFTWARE_BEHAVIOR = PREFIX + "mobile.software_behavior"
		ITUNES = PREFIX + "mobile.iTunes"
		ITUNES_DEPRECATED = PREFIX + "iTunes"
		ITUNES_STORE = PREFIX + "mobile.iTunes.store"
		ITUNES_ACCESSORIES = PREFIX + "mobile.iTunes.accessories"
		FIND_MY = PREFIX + "fmip"
		BACKUP = PREFIX + "mobile.backup"
		ACCESSIBILITY = PREFIX + "Accessibility"
		INTERNATIONALIZATION = PREFIX + "international"
		MOBILE_APPLICATION_USAGE = PREFIX + "mobile.mobile_application_usage"
		LOCKDOWN = PREFIX + "mobile.lockdownd"
		LOCKDOWN_CACHE = PREFIX + "mobile.lockdown_cache"
		WIRELESS_LOCKDOWN = PREFIX + "mobile.wireless_lockdown"
		FAIRPLAY = PREFIX + "fairplay"
		IQ_AGENT = PREFIX + "iqagent"
		PURPLE_BUDDY = PREFIX + "PurpleBuddy"
		PURPLE_BUDDY_2 = PURPLE_BUDDY.lower()
		CHAPERONE = PREFIX + "mobile.chaperone"
		THIRD_PARTY_TERMINATION = PREFIX + "mobile.third_party_termination"
		NIKITA = PREFIX + "mobile.nikita"
		XCODE_DEVELOPER_DOMAIN = PREFIX + "xcode.developerdomain"
		DATA_SYNC = PREFIX + "mobile.data_sync"
		TETHERED_SYNC = PREFIX + "mobile.tethered_sync"
		SYNC_DATA_CLASS = PREFIX + "mobile.sync_data_class"

		@classmethod
		# @cache
		def all(cls) -> Set[str]:
			return frozenset(member[1] for member in cls.__dict__.items() if member[0] == member[0].upper())

	ECID = "UniqueChipID"
	UDID = "UniqueDeviceID"
	DIE_ID = "DieID"
	CHIP_ID = "ChipID"
	SERIAL_NUMBER = "SerialNumber"
	PRODUCT_NAME = "ProductName"
	PRODUCT_TYPE = "ProductType"
	PRODUCT_VERSION = "ProductVersion"
	RELEASE_TYPE = "ReleaseType"
	HARDWARE_MODEL = "HardwareModel"
	HARDWARE_PLATFORM = "HardwarePlatform"
	MODEL_NUMBER = "ModelNumber"
	REGION_INFO = "RegionInfo"
	CLASS = "DeviceClass"
	COLOR = "DeviceColor"
	STORAGE_CAPACITY = Domain.DISK_USAGE + Domain._ + "TotalDiskCapacity"
	TELEPHONY_CAPABLE = "TelephonyCapability"
	TELEPHONY_GENERATION = "ActiveWirelessTechnology"
	SIM_STATUS = "SIMStatus"
	CARRIER_BUNDLES = "CarrierBundleInfoArray"
	PHONE_NUMBER_1 = "PhoneNumber"
	PHONE_NUMBER_2 = PHONE_NUMBER_1 + "2"
	CARRIER_1 = "Carrier"
	CARRIER_2 = CARRIER_1 + "2"
	IMEI_1 = "InternationalMobileEquipmentIdentity"
	IMEI_2 = IMEI_1 + "2"
	IMSI_1 = "InternationalMobileSubscriberIdentity"
	IMSI_2 = IMSI_1 + "2"
	ICCID_1 = "IntegratedCircuitCardIdentity"
	ICCID_2 = ICCID_1 + "2"
	IS_ESIM_1 = "SIM1IsEmbedded"
	IS_ESIM_2 = IS_ESIM_1.replace("1", "2")
	SIM_1_GID_1 = "SIMGID1"
	SIM_2_GID_1 = "SIM2GID1"
	NVRAM = "NonVolatileRAM"
	TIMESTAMP = "TimeIntervalSince1970"
	TIME_ZONE = "TimeZone"
	TIME_ZONE_OFFSET = "TimeZoneOffsetFromUTC"
	IS_SETUP = Domain.PURPLE_BUDDY_2 + Domain._ + "SetupDone"
	HAS_MDM = Domain.CHAPERONE + Domain._ + "DeviceIsChaperoned"
	HAS_ICLOUD = Domain.FIND_MY + Domain._ + "IsAssociated"
	HAS_PASSCODE = "PasswordProtected"

	class MobileGestalt(enum.EnumDict):
		PRODUCT_NAME = "marketing-name"
		DEVICE_NAME = "UserAssignedDeviceName"
		DEVICE_COLOR = "DeviceRGBColor"
		TELEPHONY_CAPABLE = "telephony"
		TELEPHONY_GENERATION = "telephony-maximum-generation"
		HAS_CELLULAR_DATA = "cellular-data"
		HAS_DATA_PLAN = "data-plan"
		IN_DIAGNOSTICS_MODE = "InDiagnosticsMode"

	class Recovery(enum.EnumDict):
		MODE = "MODE"
		ECID = "ECID"
		CHIP_ID = "CPID"
		SERIAL_NUMBER = "SRNM"
		PRODUCT_NAME = "NAME"
		PRODUCT_TYPE = "PRODUCT"
		HARDWARE_MODEL = "MODEL"
		IMEI_1 = "IMEI"

class DomainKey(enum.EnumDict):
	class FactoryDiskUsage(enum.EnumDict):
		DATA_AVAILABLE = "AmountDataAvailable"
		DATA_RESERVED = "AmountDataReserved"
		CALENDAR = "CalendarUsage"
		CAMERA = "CameraUsage"
		MEDIA_CACHE = "MediaCacheUsage"
		PHOTOS = "PhotoUsage"
		TOTAL_DATA_AVAILABLE = "TotalDataAvailable"
		TOTAL_DATA_SIZE = "TotalDataCapacity"
		TOTAL_DISK_SIZE = "TotalDiskCapacity"
		SYSTEM_FREE = "TotalSystemAvailable"
		SYSTEM_SIZE = "TotalSystemCapacity"
		VOICEMAIL = "VoicemailUsage"
		WEBAPP_CACHE = "WebAppCacheUsage"

class ChipID():
	# key: chipid in hex => "InternalID": str, "Name": str
	_data = None

	# URL of file
	_datasource = "https://gist.githubusercontent.com/jboby93/365c8a6f2905f76fbe38f2e38baf20dc/raw/chipid-dict.json"

	@classmethod
	def init(cls):
		cls._data = None
		try:
			with open("chipid-dict.json", "r") as f:
				cls._data = json.loads(f.read())
		except Exception as e:
			term.print_warning("* unable to load chipid-dict.json: %s" % str(e))

	@classmethod
	def lookup(cls, chipid):
		if cls._data is None:
			cls.init()
			if cls._data is None:
				return None
		
		if chipid in cls._data:
			return ChipID(chipid)

	def __init__(self, chipid):
		lookup = self._data[chipid]

		self._id = chipid
		self._name = self._data[chipid]["Name"]
		self._internal_id = self._data[chipid]["InternalID"]

	@property
	def id(self):
		return self._id

	@property
	def name(self):
		return self._name

	@property
	def internal_id(self):
		return self._internal_id
	
	def __repr__(self):
		dict_repr = {
			"ChipID": self.id,
			"MarketingName": self.name,
			"InternalID": self.internal_id
		}
		return f"{self.__class__.__name__}({dict_repr})"
	

class MobileApp():
	def __init__(self, **kwargs):
		self._id = kwargs.pop("CFBundleIdentifier")
		self._name = kwargs.pop("CFBundleDisplayName")
		self._version = kwargs.pop("CFBundleShortVersionString", None)

	@property
	def id(self):
		return self._id

	@property
	def name(self):
		return self._name

	@property
	def version(self):
		return self._version
	
	def __repr__(self):
		dict_repr = {
			"DisplayName": self.name,
			"BundleID": self.id,
			"BundleVersion": self.version
		}
		return f"{self.__class__.__name__}({dict_repr})"
	
	def __str__(self):
		if self.version:
			return f"{self.name} v{self.version} ({self.id})"
		else:
			return f"{self.name} ({self.id})"

	# probably not a complete list, but would probably help shorten the list of system *apps*
	def is_system_service_bundle(self) -> bool:
		return self.id in [
			"com.apple.CarClosures",
			"com.apple.RecoverDeviceUI",
			"com.apple.CoreAuthUI",
			"com.apple.SOSBuddy",
			"com.apple.PreviewShell",
			"com.apple.AccessorySetupUI",
			"com.apple.Batteries",
			"com.apple.ClarityPhotos",
			"com.apple.AppDeletionUIHost",
			"com.apple.UIKit.ColorPickerUIService",
			"com.apple.UIKit.FontPickerUIService",
			"com.apple.feedback.remote",
			"com.apple.findmy.remoteuiservice",
			"com.apple.PassbookUISceneService",
			"com.apple.ContactsUI.Carousel",
			"com.apple.ScreenContinuityShell",
			"com.apple.StoreDemoViewService",
			"com.apple.PreBoard",
			"com.apple.AskPermissionUI",
			"com.apple.AuthKitUIService",
			"com.apple.DiagnosticsReporter",
			"com.apple.BarcodeScanner",
			"com.apple.ProximityReaderUIService",
			"com.apple.PassbookSecureUIService",
			"com.apple.susuiservice",
			"com.APSQA.MetisTest",
			"com.apple.ActivityProgress.ActivityProgressUI",
			"com.apple.AutoSettings",
			"com.apple.CarRadio",
			"com.apple.CameraOverlayAngel",
			"com.apple.ProximityReaderSceneUI",
			"com.apple.icloud.FindMyDevice.FindMyExtensionContainer",
			"com.apple.MobileReplayer",
			"com.apple.CarTirePressure",
			"com.apple.ClockAngel",
			"com.apple.dockkit.pairinguiservice",
			"com.apple.sidecar",
			"com.apple.datadetectors.DDActionsService",
			"com.apple.SpringBoardEducation",
			"com.apple.ShortcutsUI",
			"com.apple.SharedWebCredentialViewService",
			"com.apple.MTLReplayer",
			"com.apple.chrono.WidgetRenderer-CarPlay",
			"com.apple.CarPlaySetupApp",
			"com.apple.FontInstallViewService",
			"com.apple.musicrecognition",
			"com.apple.social.SLYahooAuth",
			"com.apple.mobilesms.compose",
			"com.apple.accessibility.MagnifierAngel",
			"com.apple.iMessageAppsViewService",
			"com.apple.ScreenshotServicesService",
			"com.apple.FinanceStub",
			"com.apple.ScreenSharingViewService",
			"com.apple.ScreenTimeUnlock",
			"com.apple.HealthENLauncher",
			"com.apple.PublicHealthRemoteUI",
			"com.apple.TVSetupUIService",
			"com.apple.Home.HomeControlService",
			"com.apple.AMSUIAuthenticationViewService",
			"com.apple.webapp",
			"com.apple.CBRemoteSetup",
			"com.apple.gamecenter.GameCenterUIService",
			"com.apple.WebContentFilter.remoteUI.WebContentAnalysisUI",
			"com.apple.MBHelperApp",
			"com.apple.Spotlight",
			"com.apple.StickerKit.StickerPickerService",
			"com.apple.SafetyMonitorApp",
			"com.apple.ContactsUI.LimitedAccessPromptView",
			"com.apple.InputUI",
			"com.apple.RemotePassUIService",
			"com.apple.CTNotifyUIService",
			"com.apple.MailCompositionService",
			"com.apple.ClarityCamera",
			"com.apple.HearingApp",
			"com.apple.purplebuddy",
			"com.apple.APSUIApp",
			"com.apple.TVRemoteUIService",
			"com.apple.CarPlaySettings",
			"com.apple.AskToMessagesHost",
			"com.apple.PassbookUIService",
			"com.apple.FMDMagSafeSetupRemoteUI",
			"com.apple.CheckerBoard",
			"com.apple.FinanceUIService",
			"com.apple.RemoteiCloudQuotaUI",
			"com.apple.EyeReliefUI",
			"com.apple.FaceTimeLinkTrampoline",
			"com.apple.ShazamEventsApp",
			"com.apple.MediaRemoteUIService",
			"com.apple.SystemPaperViewService",
			"com.apple.Photos.PhotosUIService",
			"com.apple.HDSViewService",
			"com.apple.ClipViewService",
			"com.apple.GuestUserHandoverSetup",
			"com.apple.shortcuts.runtime",
			"com.apple.CarPlaySplashScreen",
			"com.apple.replaykitangel",
			"com.apple.SESUIServiceApp",
			"com.apple.family",
			"com.apple.WritingToolsUIService",
			"com.apple.Home.HomeUIService",
			"com.apple.airplayreceiver",
			"com.apple.InCallService",
			"com.apple.systemactions",
			"com.apple.SharingViewService",
			"com.apple.ScreenTimeWidgetApplication",
			"com.apple.VSViewService",
			"com.apple.FCAuthenticationUI",
			"com.apple.CredentialSharingService",
			"com.apple.TrustMe",
			"com.apple.AppleIDSetupUIService",
			"com.apple.AppProtectionUIHost",
			"com.apple.HealthPrivacyService",
			"com.apple.ProductKitViewer",
			"com.apple.CarPlayWallpaper",
			"com.apple.Sharing.AirDropUI",
			"com.apple.SleepLockScreen",
			"com.apple.CarCamera",
			"com.apple.chrono.WidgetRenderer-Default",
			"com.apple.SubcredentialUIService",
			"com.apple.PosterBoard",
			"com.apple.GameOverlayUI",
			"com.apple.SharingUIService",
			"com.apple.CarCharge",
			"com.apple.SIMSetupUIService",
			"com.apple.CTCarrierSpaceAuth",
			"com.apple.PASViewService",
			"com.apple.PCViewService",
			"com.apple.CarTrip",
			"com.apple.EventViewService",
			"com.apple.WebSheet",
			"com.apple.WorkoutRemoteViewService",
			"com.apple.AMSEngagementViewService",
			"com.apple.DiagnosticsService",
			"com.apple.TVAccessViewService",
			"com.apple.PDUIApp",
			"com.apple.SafariViewService",
			"com.apple.AppDistributionLaunchAngel",
			"com.apple.MusicUIService",
			"com.apple.icq",
			"com.apple.DemoApp",
			"com.apple.AXUIViewService",
			"com.apple.AuthenticationServicesUI",
			"com.apple.MediaRemoteUI",
			"com.apple.ctkui",
			"com.apple.ios.StoreKitUIService",
			"com.apple.AdaptiveMusicApp",
			"com.apple.HomeCaptiveViewService",
			"com.apple.CompanionViewService",
			"com.apple.GAXApp",
			"com.apple.AAUIViewService",
			"com.apple.HeadphoneProxService",
			"com.apple.GameCenterRemoteAlert",
			"com.apple.HealthENBuddy",
			"com.apple.AppSSOUIService",
			"com.apple.MomentsUIService",
			"com.apple.NewDeviceSetupUIService",
			"com.apple.AXRemoteViewService",
			"com.apple.CompassCalibrationViewService",
			"com.apple.ContinuityCaptureShieldUI",
			"com.apple.PeopleMessageService",
			"com.apple.FTMInternal",
			"com.apple.TDGSharingViewService",
			"com.apple.CarClimate",
			"com.apple.AccountAuthenticationDialog",
			"com.apple.NewDeviceOutreachApp",
			"com.apple.BusinessChatViewService",
			"com.apple.BacklinkIndicator"
		]