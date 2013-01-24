"""
What's next...
[Bus]
Remove Dense bus and use only Sparse implementation (don't use [], use only {})
Dense addr would be still possible as the output by stepping addr._increment().
Addresses are:
    stor_addr = stored address representation '$first-$second-...-$ZZZ'
    addr = internal address representation [$first, $second, ..., $ZZZ]
    device_addr = device{$param1:$first, $param2:$second, ..., $paramZZZ, $ZZZ}
"""
"""
[Testing]
import sys
sys.path.append('/home/medic/Work/AAA/autotest/tmp/my_git')
from kvm_devices import *

del(sys.modules['kvm_devices'])
from kvm_devices import *
"""

import os
import re


HELP = os.popen('qemu-kvm -h').read()
DEVICES = os.popen('qemu-kvm -device ? 2>&1').read()


def NoneOrInt(value):
    """ Helper fction which returns None or int() """
    if not value:   # "", None, False
        return None
    elif isinstance(value, str) and value.isdigit():
        return int(value)
    elif isinstance(value, int):
        return value
    else:
        raise TypeError("This parameter have to be number or none")


class virt_vm:
    class VMDeviceNotSupportedError(Exception):
        pass


class VM:
    def __init__(self, name="vm1"):
        self.name = name


class DeviceError(Exception):
    pass


class QDevUsbs(object):
    def __init__(self, qdev):
        self.qdev = qdev

    def define_by_variables(self, usb_id, usb_type, multifunction=False,
                            masterbus=None, firstport=None, freq=None,
                            max_ports=None, pci_addr=None):

        if max_ports is None:
            max_ports = 6
        if not self.qdev.has_option("device"):
            # Okay, for the archaic qemu which has not device parameter,
            # just return a usb uhci controller.
            # If choose this kind of usb controller, it has no name/id,
            # and only can be created once, so give it a special name.
            usb = QStringDevice("oldusb", cmdline="-usb",
                                child_bus=QUSBBus(2, 'usb.0', 'uhci', usb_id))
            return [usb]

        if not self.qdev.has_device(usb_type):
                raise virt_vm.VMDeviceNotSupportedError(self.qdev.vmname,
                                                        usb_type)

        usb = QDevice({}, usb_id, {'type': 'pci'},
                      QUSBBus(max_ports, '%s.0' % usb_id, usb_type, usb_id))
        new_usbs = [usb]    # each usb dev might compound of multiple devs
        # TODO: Add 'bus' property (it was not in the original version)
        usb.set_param('driver', usb_type)
        usb.set_param('id', usb_id)
        usb.set_param('masterbus', masterbus)
        usb.set_param('firstport', firstport)
        usb.set_param('freq', freq)
        usb.set_param('addr', pci_addr)

        if usb_type == "ich9-usb-ehci1":
            # this slot is composed in PCI so it won't go to internal repr
            usb.parent_bus = ()
            usb.set_param('addr', '1d.7')
            usb.set_param('multifunction', 'on')
            for i in xrange(3):
                new_usbs.append(QDevice({}, usb_id))
                new_usbs[-1].set_param('id', '%s.%d' % (usb_id, i))
                new_usbs[-1].set_param('multifunction', 'on')
                new_usbs[-1].set_param('masterbus', '%s.0' % usb_id)
                new_usbs[-1].set_param('driver', 'ich9-usb-uhci%d' % i)
                new_usbs[-1].set_param('addr', '1d.%d' % i)
                new_usbs[-1].set_param('firstport', 2 * i)
        return new_usbs

    def define_by_params(self, usb_name, params):
        return self.define_by_variables(usb_name,
                                        params.get('usb_type'),
                                        params.get('multifunction'),
                                        params.get('masterbus'),
                                        params.get('firstport'),
                                        params.get('freq'),
                                        params.get('max_ports'),
                                        params.get('pci_addr'))


class QDevImages(object):
    """
    Helper for defining images.
    @warning: In order to create indexes (HBAs, disk ids) properly you have to
              insert device into qdev before creating another disk device!
    """
    def __init__(self, qdev):
        self.qdev = qdev

    def define_by_variables(self, name, filename, index=None, fmt=None,
                      cache=None, werror=None, rerror=None, serial=None,
                      snapshot=None, boot=None, blkdebug=None, bus=None,
                      unit=None, port=None, bootindex=None, removable=None,
                      min_io_size=None, opt_io_size=None,
                      physical_block_size=None, logical_block_size=None,
                      readonly=None, scsiid=None, lun=None, aio=None,
                      strict_mode=None, media=None, imgfmt=None,
                      pci_addr=None, scsi_hba="virtio-scsi-pci"):
        # All related devices
        devices = []

        supports_device = self.qdev.has_option("device")

        if strict_mode is None:
            strict_mode = self.qdev.strict_mode
        if strict_mode:
            if cache is None:       cache = "none"
            if removable is None:   removable = "yes"
            if aio is None:         aio = "native"
            if media is None:       media = "disk"

        # TODO: Unify drive params
        bus = NoneOrInt(bus)     # First level
        unit = NoneOrInt(unit)   # Second level
        port = NoneOrInt(port)   # Third level
        # Compatibility with old params - scsiid, lun
        if unit is None:
            unit = NoneOrInt(scsiid)
        if port is None:
            port = NoneOrInt(lun)

        # fmt: ide, scsi, virtio, scsi-hd, ahci, usb1,2,3 + hba
        # device: ide-drive, usb-storage, scsi-hd, scsi-cd, virtio-blk-pci
        # bus: ahci, virtio-scsi-pci, USB

        # HBA
        if not supports_device:
            # TODO: Add bus representation as it's added automatically
            # if ide: only 1x
            # if scsi: when not free add next
            pass
        elif fmt == "ahci":
            _bus = bus
            if bus is None:    # None
                #@return: name of matching free bus | index of next bus
                bus = self.qdev.get_first_free_bus({'type': 'ahci'},
                                                   [unit, port])
                if bus is None:
                    bus = self.qdev.idx_of_next_named_bus('ahci')
                else:
                    bus = bus.busid
            if isinstance(bus, int):    # Bus might not yet exist
                for bus_name in self.qdev.list_missing_named_buses('ahci',
                                                        'ahci', bus + 1):
                    devices.append(QDevice({'id': bus_name, 'driver': 'ahci'},
                                           None, {'type': 'pci'},
                                           QAHCIBus(bus_name, name)))
                bus = 'ahci%d' % bus
                if unit is not None:
                    bus += '.%d' % unit
            if unit is None and _bus is None:
                bus = None  # Don't assign bus when addr=(None, None)
        elif fmt.startswith('scsi-'):
            # TODO: When lun is None use 0 instead as it's not used by qemu arg
            # parser to assign luns (when there is no place it incr scsiid
            # in non strict_mode (strict_mode can assign any scsiid+lun
            _scsi_hba = scsi_hba.replace('-', '_') + '%s.0'
            _bus = bus
            if bus is None:
                bus = self.qdev.get_first_free_bus({'type': scsi_hba},
                                                   [unit, port])
                if bus is None:
                    bus = self.qdev.idx_of_next_named_bus(_scsi_hba)
                else:
                    bus = bus.busid
            if isinstance(bus, int):
                for bus_name in self.qdev.list_missing_named_buses(
                                            _scsi_hba, scsi_hba, bus + 1):
                    # TODO: Make list of ranges of various scsi_hbas.
                    #       This is based on virtio-scsi-pci
                    devices.append(QDevice({'id': bus_name, 'driver': scsi_hba}
                                           , None, {'type': 'pci'},
                                           QSCSIBus(bus_name)))
                bus = _scsi_hba % bus
        # Drive
        # TODO: Add QRHDrive and PCIDrive for hotplug purposes
        devices.append(QDrive(name))
        devices[-1].set_param('if', 'none')
        devices[-1].set_param('cache', cache)
        devices[-1].set_param('rerror', rerror)
        devices[-1].set_param('werror', werror)
        devices[-1].set_param('serial', serial)
        devices[-1].set_param('boot', boot, bool)
        devices[-1].set_param('snapshot', snapshot, bool)
        devices[-1].set_param('readonly', readonly, bool)
        devices[-1].set_param('aio', aio)
        devices[-1].set_param('media', media)
        devices[-1].set_param('format', imgfmt)
        if blkdebug is not None:
            devices[-1].set_param('file', 'blkdebug:%s:%s' % (blkdebug,
                                                              filename))
        else:
            devices[-1].set_param('file', filename)
        if not supports_device:
            if fmt not in ('ide', 'scsi', 'sd', 'mtd', 'floppy', 'pflash',
                           'virtio'):
                raise virt_vm.VMDeviceNotSupportedError(self.qdev.vmname,
                                                        fmt)
            devices[-1].set_param('if', fmt)
            devices[-1].set_param('index', index)
            devices[-1].parent_bus += ({'type': fmt},)
            if fmt == 'virtio':
                devices[-1].set_param('addr', pci_addr)
                devices[-1].parent_bus += ({'type': 'pci'},)

        # Device
        devices.append(QDevice({}, name))
        devices[-1].parent_bus += ({'busid': 'drive_%s' % name},)   # drive
        devices[-1].set_param('id', name)
        devices[-1].set_param('bus', bus)
        devices[-1].set_param('drive', 'drive_%s' % name)
        devices[-1].set_param('logical_block_size', logical_block_size)
        devices[-1].set_param('physical_block_size', physical_block_size)
        devices[-1].set_param('min_io_size', min_io_size)
        devices[-1].set_param('bootindex', bootindex)
        if fmt != 'virtio':
            devices[-1].set_param('serial', serial)
            devices[-1].set_param('removable', removable)
        if not fmt.startswith('scsi-'): # scsi sets hba later in the code
            devices[-1].parent_bus += ({'type': fmt},)  # hba
        if fmt == "ahci":
            devices[-1].set_param('driver', 'ide-drive')
            devices[-1].set_param('unit', port)
        elif fmt.startswith('scsi-'):
            devices[-1].parent_bus += ({'type': scsi_hba},)
            devices[-1].set_param('driver', fmt)
            devices[-1].set_param('scsi_id', unit)
            devices[-1].set_param('lun', port)
            if strict_mode:
                devices[-1].set_param('channel', 0)

        return devices

    def define_by_params(self, params, force_media=None, strict_mode=None):
        """
        Process disk arguments to qemu -drive command.
        @note: To skip the argument use None, to disable it use False
        @note: Strictly bool options accept "yes", "on" and True ("no"...)
        @note: Options starting with '_' are optional and used only when
               strict_mode == True
        @param hlp: qemu -h output
        @param filename: Path to the disk file
        @param index: drive index (used for generating names)
        @param fmt: drive subsystem type (ide, scsi, virtio, usb2, ...)
        @param cache: disk cache (none, writethrough, writeback)
        @param werror: What to do when write error occurs (stop, ...)
        @param rerror: What to do when read error occurs (stop, ...)
        @param serial: drive serial number ($string)
        @param snapshot: use snapshot? ($bool)
        @param boot: is bootable? ($bool)
        @param blkdebug: use blkdebug (None, blkdebug_filename)
        @param bus: bus on which this drive is located ($string)
        @param port: port on which this drive is located ($string)
        @param bootindex: device boot priority ($int)
        @param removable: can the drive be removed? ($bool)
        @param min_io_size: Min allowed io size
        @param opt_io_size: Optimal io size
        @param physical_block_size: set physical_block_size ($int)
        @param logical_block_size: set logical_block_size ($int)
        @param readonly: set the drive readonly ($bool)
        @param scsiid: set drive scsi_id ($int)
        @param lun: set the drive lun ($int)
        @param aio: set the type of async IO (native, threads, ..)
        @param strict_mode: enforce parameters starting with '_' ($bool)
        @param media: type of the media (disk, cdrom, ...)
        @param imgfmt: image format (qcow2, raw, ...)
        @param pci_addr: drive pci address ($int)
        """
        pass


class QBaseDevice(object):
    """ Base class of qemu objects """
    def __init__(self, dev_type="QBaseDevice", params={}, aobject=None,
                 parent_bus=(), child_bus=()):
        """
        @param dev_type: type of this component
        @param params: component's parameters
        @param aobject: Autotest object which is associated with this device
        @param parent_bus: Dict specifying the bus (aobject, type, busid)
        @param child_bus: bus, which this device provides
        """
        self.aid = None         # unique per VM id
        self.type = dev_type    # device type
        self.aobject = aobject  # related autotest object
        self.parent_bus = parent_bus    # aobject, type, busid
        self.child_bus = child_bus      # bus provided by this device
        self.params = {}
        for key, value in params.iteritems():
            self.set_param(key, value)

    def set_param(self, option, value, option_type=None):
        """
        Set device param using qemu notation ("on", "off" instead of bool...)
        @param option: which option's value to set
        @param value: new value
        @param option_type: type of the option (bool)
        """
        if option_type is bool:
            if value in ['yes', 'on', True]:
                self.params[option] = "on"
            elif value in ['no', 'off', False]:
                self.params[option] = "off"
        elif value and isinstance(value, bool):
            self.params[option] = "on"
        elif value or value == 0:
            self.params[option] = value
        elif value is None and option in self.params:
            del(self.params[option])

    def get_param(self, option):
        """ @return: object param """
        return self.params.get(option)

    def __contains__(self, option):
        return option in self.params

    def __str__(self):
        return self.str_short()

    def str_short(self):
        if self.get_qid():  # Show aid only when it's based on qid
            if self.get_aid():
                return self.get_aid()
            else:
                return "q'%s'" % self.get_qid()
        elif self._get_alternative_name():
            return "a'%s'" % self._get_alternative_name()
        else:
            return "t'%s'" % self.type

    def str_long(self):
        out = """%s
  aid = %s
  aobject = %s
  parent_bus = %s
  child_bus = %s
  params:""" % (self.type, self.aid, self.aobject, self.parent_bus,
                self.child_bus)
        for key, value in self.params.iteritems():
            out += "\n    %s = %s" % (key, value)
        return out + '\n'

    def _get_alternative_name(self):
        """ @return: alternative object name """
        return None

    def get_qid(self):
        """ @return: qemu_id """
        return self.params.get('id', '')

    def get_aid(self):
        """ @return: per VM unique autotest_id """
        return self.aid

    def _set_aid(self, aid):
        """@param aid: new autotest id for this device"""
        self.aid = aid

    def cmdline(self):
        raise NotImplementedError

    def hotplug(self):
        raise NotImplementedError

    def unplug(self):
        raise NotImplementedError

    def readconfig(self):   # NotImplemented yet
        raise NotImplementedError


class QStringDevice(QBaseDevice):
    """
    General device which allows to specify methods by fixed or parametrizable
    strings in this format:
      "%(type)s,id=%(id)s,addr=%(addr)s" -- params will be used to subst %()s
    """
    def __init__(self, dev_type, params={}, aobject=None,
                 parent_bus=(), child_bus=(), cmdline="", hotplug="",
                 unplug="", readconfig=""):
        """
        @param dev_type: type of this component
        @param params: component's parameters
        @param aobject: Autotest object which is associated with this device
        @param parent_bus:
        @param child_bus: bus, which this device provides
        @param cmdline: cmdline string
        @param hotplug: hotplug string
        @param unplug: unplug string
        @param readconfig: readconfig string
        """
        super(QStringDevice, self).__init__(dev_type, params, aobject,
                                            parent_bus, child_bus)
        self.type = dev_type
        self._cmdline = cmdline
        self._hotplug = hotplug
        self._unplug = unplug
        self._readconfig = readconfig

    def cmdline(self):
        try:
            return self._cmdline % self.params
        except KeyError, details:
            raise KeyError("Param %s required for cmdline is not present in %s"
                           % (details, self.str_long()))

    def hotplug(self):
        try:
            return self._hotplug % self.params
        except KeyError, details:
            raise KeyError("Param %s required for hotplug is not present in %s"
                           % (details, self.str_long()))

    def unplug(self):
        try:
            return self._unplug % self.params
        except KeyError, details:
            raise KeyError("Param %s required for unplug is not present in %s"
                           % (details, self.str_long()))

    def readconfig(self):
        try:
            return self._readconfig % self.params
        except KeyError, details:
            raise KeyError("Param %s required for readconfig is not present in"
                           " %s" % (details, self.str_long()))


class QCustomDevice(QBaseDevice):
    """
    Representation of the '-$option $param1=$value1,$param2...' qemu object.
    This representation handles only cmdline and readconfig outputs.
    """
    def __init__(self, dev_type, params={}, aobject=None,
                 parent_bus=(), child_bus=()):
        """
        @param dev_type: The desired -$option parameter (device, chardev, ..)
        """
        super(QCustomDevice, self).__init__(dev_type, params, aobject,
                                            parent_bus, child_bus)

    def cmdline(self):
        out = "-%s " % self.type
        for key, value in self.params.iteritems():
            out += "%s=%s," % (key, value)
        if out[-1] == ',':
            out = out[:-1]
        return out

    def readconfig(self):
        out = "[%s" % self.type
        if self.get_qid():
            out += ' "%s"' % self.get_qid()
        out += "]\n"
        for key, value in self.params.iteritems():
            if key == "id":
                continue
            out += '  %s = "%s"\n' % (key, value)
        return out


class QDevice(QCustomDevice):
    """
    Representation of the '-device' qemu object. It supports all methods.
    @note: Use driver format in full form - 'driver' = '...' (usb-ehci, ide-hd)
    """
    def __init__(self, params={}, aobject=None, parent_bus=(),
                 child_bus=()):
        super(QDevice, self).__init__("device", params, aobject, parent_bus,
                                      child_bus)

    def _get_alternative_name(self):
        if self.params.get('driver'):
            return self.params.get('driver')

    def hotplug(self):
        out = "device_add "
        for key, value in self.params.iteritems():
            out += "%s=%s," % (key, value)
        if out[-1] == ',':
            out = out[:-1]
        return out

    def unplug(self):
        if self.get_qid():
            return "device_del %s" % self.get_qid()


class QDrive(QCustomDevice):
    """
    Representation of the '-drive' qemu object. It supports all methods.
    """
    def __init__(self, aobject):
        child_bus = QDriveBus('drive_%s' % aobject, aobject)
        super(QDrive, self).__init__("drive", {}, aobject, (),
                                      child_bus)
        self.params['id'] = 'drive_%s' % aobject

    def set_param(self, option, value, option_type=None):
        if option == 'id':
            raise KeyError("Drive ID is automatically created from aobject. %s"
                           % self)
        super(QDrive, self).set_param(option, value, option_type)

    def hotplug(self):
        out = "drive_add auto "
        for key, value in self.params.iteritems():
            out += "%s=%s," % (key, value)
        if out[-1] == ',':
            out = out[:-1]
        return out

    def unplug(self):
        if self.get_qid():
            return "drive_del %s" % self.get_qid()


class QBaseBus(object):
    """ Base class for Bus representation objects """
    def __init__(self, busid, bus_type, aobject=None):
        self.busid = busid
        self.type = bus_type
        self.aobject = aobject
        self.bus = None
        self.badbus = {}

    def __str__(self):
        return self.str_short()

    def str_short(self):
        return "%s(%s): %s  %s" % (self.busid, self.type, self._str_devices(),
                                   self._str_bad_devices())

    def str_long(self):
        return "Bus %s, type=%s\nSlots:\n%s\n%s" % (self.busid, self.type,
                    self._str_devices_long(), self._str_bad_devices_long())

    def _str_devices(self):
        return self.bus

    def _str_bad_devices(self):
        return self.badbus

    def _str_devices_long(self):
        out = ""
        if hasattr(self.bus, 'iteritems'):
            for addr, dev in self.bus.iteritems():
                out += '%s< %4s >%s\n  ' % ('-' * 15, self._addr2stor(addr),
                                            '-' * 15)
                if isinstance(dev, str):
                    out += '"%s"\n  ' % dev
                else:
                    out += dev.str_long().replace('\n', '\n  ')
                    out = out[:-3]
                out += '\n'
        elif hasattr(self.bus, '__iter__'):
            for addr in xrange(len(self.bus)):
                dev = self.bus[addr]
                out += '%s< %4s >%s\n  ' % ('-' * 15, self._addr2stor(addr),
                                            '-' * 15)
                if hasattr(dev, 'str_long'):
                    out += dev.str_long().replace('\n', '\n  ')
                    out = out[:-3]
                elif isinstance(dev, str):
                    out += '"%s"' % dev
                else:
                    out += "%s  " % dev
                out += '\n'
        elif hasattr(self.bus, 'str_long'):
            out = self.bus.str_long()
        else:
            out = "%s\n" % self.bus
        return out

    def _addr2stor(self, addr):
        return addr

    def _str_bad_devices_long(self):
        out = ""
        if hasattr(self.badbus, 'iteritems'):
            for addr, dev in self.badbus.iteritems():
                out += '%s< %4s >%s\n  ' % ('-' * 15, self._addr2stor(addr),
                                            '-' * 15)
                if isinstance(dev, str):
                    out += '"%s"\n  ' % dev
                else:
                    out += dev.str_long().replace('\n', '\n  ')
                    out = out[:-3]
                out += '\n'
        elif hasattr(self.badbus, '__iter__'):
            for addr in xrange(len(self.badbus)):
                dev = self.badbus[addr]
                out += '%s< %4s >%s\n  ' % ('-' * 15, self._addr2stor(addr),
                                            '-' * 15)
                if hasattr(dev, 'str_long'):
                    out += dev.str_long().replace('\n', '\n  ')
                    out = out[:-3]
                elif isinstance(dev, str):
                    out += '"%s"' % dev
                else:
                    out += "%s  " % dev
                out += '\n'
        elif hasattr(self.badbus, 'str_long'):
            out = self.badbus.str_long()
        else:
            out = "%s\n" % self.badbus
        return out

    def _get_free_slot(self, addr):
        raise NotImplementedError

    def _insert_oor(self, device, addr):
        if addr in self.badbus:
            i = 0
            while "%s(%d)" % (addr, i) in self.badbus:
                i += 1
            addr = "%s(%d)" % (addr, i)
        self.badbus[addr] = device

    def _insert_used(self, device, addr):
        i = 2
        while "%s(%dx)" % (addr, i) in self.badbus:
            i += 1
        self.badbus["%s(%dx)" % (addr, i)] = device

    def _remove_bad(self, device):
        if device in self.badbus.iteritems():
            remove = None
            for key, item in self.badbus.iteritems():
                if item is device:
                    remove = key
                    break
            if remove:
                del(self.badbus[remove])
                return True
        return False

    def reserve(self, device):
        raise NotImplementedError

    def insert(self, device):
        raise NotImplementedError

    def remove(self, addr):
        raise NotImplementedError


class QDriveBus(QBaseBus):
    def __init__(self, busid, aobject=None):
        super(QDriveBus, self).__init__(busid, '__QDrive', aobject)
        self.bus = None

    def insert(self, device, strict_mode=False, force=False):
        """
        True - Success
        False - Incorrect addr/busid
        None - No free slot
        string - Force add passed, returned string is message of errors
        """
        # FIXME: Check 'drive'
        err = ""
        if (device.get_param('drive') and
                        device.get_param('drive') != self.busid):
            if not force:
                return False
            else:
                err += "BusID, "
        if self.bus:
            if not force:
                return None
            err += "UsedSlot, "
            self._insert_used(device, 0)
        else:
            self.bus = device
        # Always set drive, it's required
        device.set_param('drive', self.busid)
        if err:
            # Device was force added with errors
            err = ("Force adding device %s into %s (errors: %s)"
                   % (device, self, err))
            return err
        return True

    def remove(self, device):
        if self.bus is device:
            self.bus = None
        else:
            return self._remove_bad(device)
        return True


class QDense1DBus(QBaseBus):
    def __init__(self, bus_item, addr_item, length, busid, bus_type,
                 aobject=None):
        super(QDense1DBus, self).__init__(busid, bus_type, aobject)
        self.bus = [None] * length      # Normal bus records
        self.badbus = {}                  # Bad bus records
        self.addr_item = addr_item
        self.bus_item = bus_item

    def _str_devices(self):
        out = "["
        for device in self.bus:
            out += "%s," % device
        if out[-1] == ',':
            out = out[:-1]
        return out + ']'

    def _str_bad_devices(self):
        out = '{'
        for addr, device in self.badbus.iteritems():
            out += "%s:" % self._addr2stor(addr)
            out += "%s," % device
        if out[-1] == ',':
            out = out[:-1]
        return out + '}'

    def _addr2stor(self, addr):
        if addr is None:
            return None
        else:
            return str(addr)

    def _dev2addr(self, device):
        return NoneOrInt(device.get_param(self.addr_item))

    def _get_free_slot(self, addr):
        if addr is None:
            for addr in xrange(len(self.bus)):
                if self.bus[addr] == None:
                    return addr
        elif isinstance(addr, (tuple, list)):
            for i in addr:
                if i > len(self.bus) or i < 0:
                    return False
                if self.bus[i] == None:
                    return i
        elif not (addr < len(self.bus) and addr >= 0):
            return False
        elif self.bus[addr] == None or self.bus[addr] == "reserved":
                return addr
        return None

    def _check_bus(self, device):
        if (device.get_param(self.bus_item) and
                    device.get_param(self.bus_item) != self.busid):
            return False
        else:
            return True

    def _set_device_props(self, device, addr):
        device.set_param(self.bus_item, self.busid)
        device.set_param(self.addr_item, self._addr2stor(addr))

    def _update_device_props(self, device, addr):
        if device.get_param(self.bus_item):
            device.set_param(self.bus_item, self.busid)
        if device.get_param(self.addr_item):
            device.set_param(self.addr_item, self._addr2stor(addr))

    def insert(self, device, strict_mode=False, force=False):
        """
        True - Success
        False - Incorrect addr/busid
        None - No free slot
        string - Force add passed, returned string is message of errors
        """
        err = ""
        if not self._check_bus(device):
            if force:
                err += "BusId, "
                device.set_param(self.bus_item, self.busid)
            else:
                return False
        _addr = self._dev2addr(device)
        addr = self._get_free_slot(_addr)
        if addr is None:
            if force:
                if _addr is None:
                    err += "NoFreeSlot, "
                    _addr = len(self.bus) - 1
                    self._insert_used(device, _addr)
                elif isinstance(_addr, (list, tuple)):
                    err += "NoFreeCustomSlot, "
                    _addr = _addr[-1]
                    self._insert_used(device, _addr)
                else:   # used slot
                    err += "UsedSlot, "
                    self._insert_used(device, _addr)
            else:
                return None
        if addr is False:
            if force:
                if isinstance(_addr, (list, tuple)):
                    _addr = _addr[-1]
                err += "BadAddr(%s), " % _addr
                self._insert_oor(device, _addr)
            else:
                return False
        else:
            self.bus[addr] = device
        if strict_mode:     # Always set full address in strict_mode
            self._set_device_props(device, addr)
        else:
            self._update_device_props(device, addr)
        if err:
            # Device was force added with errors
            err = ("Force adding device %s into %s (errors: %s)"
                   % (device, self, err[:-2]))
            return err
        return True

    def remove(self, device):
        if device in self.bus:
            self.bus[self.bus.index(device)] = None
        else:
            return self._remove_bad(device)
        return True


class QSparseBus(QBaseBus):
    """
    Universal bus representation
    used addresses:
    stor_addr = stored address representation '$first-$second-...-$ZZZ'
    addr = internal address representation [$first, $second, ..., $ZZZ]
    device_addr = device{$param1:$first, $param2:$second, ..., $paramZZZ, $ZZZ}
    """
    def __init__(self, bus_item, addr_spec, busid, bus_type, aobject=None):
        """
        @param bus_item: Name of the parameter which specifies bus (bus)
        @param addr_spec: Bus address specification [names][lengths]
        @param busid: id of the bus (pci.0)
        @param bus_type: type of the bus (pci)
        @param aobject: Related autotest object (image1)
        """
        self.busid = busid
        self.type = bus_type
        self.aobject = aobject
        self.bus = {}                       # Normal bus records
        self.badbus = {}                    # Bad bus records
        self.bus_item = bus_item            # bus param name
        self.addr_items = addr_spec[0]      # [names][lengths]
        self.addr_lengths = addr_spec[1]

    def _str_devices(self):
        out = '{'
        for addr in sorted(self.bus.keys()):
            out += "%s:" % addr
            out += "%s," % self.bus[addr]
        if out[-1] == ',':
            out = out[:-1]
        return out + '}'

    def _str_bad_devices(self):
        out = '{'
        for addr in sorted(self.badbus.keys()):
            out += "%s:" % addr
            out += "%s," % self.badbus[addr]
        if out[-1] == ',':
            out = out[:-1]
        return out + '}'

    def _increment_addr(self, addr, last_addr=None):
        if not last_addr:
            last_addr = [0] * len(self.addr_lengths)
        i = -1
        while True:
            if i < -len(self.addr_lengths):
                return False
            if addr[i] is not None:
                i -= 1
                continue
            last_addr[i] += 1
            if last_addr[i] < self.addr_lengths[i]:
                return last_addr
            last_addr[i] = 0
            i -= 1

    def _addr2stor(self, addr):
        out = ""
        for value in addr:
            if value is None:
                out += '*-'
            else:
                out += '%s-' % value
        if out:
            return out[:-1]
        else:
            return "*"

    def _dev2addr(self, device):
        addr = []
        for key in self.addr_items:
            addr.append(device.get_param(key))
        return addr

    def _get_free_slot(self, addr_pattern):
        # init
        use_reserved = True
        if addr_pattern is None:
            addr_pattern = [None] * len(self.addr_lengths)
        # set first usable addr_pattern
        last_addr = addr_pattern[:]
        if None in last_addr:  # Address is not fully specified
            use_reserved = False    # Use only free address
            for i in xrange(len(last_addr)):
                if last_addr[i] is None:
                    last_addr[i] = 0
        # Check the addr_pattern ranges
        for i in xrange(len(self.addr_lengths)):
            if last_addr[i] < 0 or last_addr[i] >= self.addr_lengths[i]:
                return False
        # Increment addr_pattern until free match is found
        while last_addr is not False:
            if self._addr2stor(last_addr) not in self.bus:
                return last_addr
            if (use_reserved and
                        self.bus[self._addr2stor(last_addr)] == "reserved"):
                return last_addr
            last_addr = self._increment_addr(addr_pattern, last_addr)
        return None     # No free matching address found

    def _check_bus(self, device):
        if (device.get_param(self.bus_item) and
                    device.get_param(self.bus_item) != self.busid):
            return False
        else:
            return True

    def _set_device_props(self, device, addr):
        device.set_param(self.bus_item, self.busid)
        for i in xrange(len(self.addr_items)):
            device.set_param(self.addr_items[i], addr[i])

    def _update_device_props(self, device, addr):
        if device.get_param(self.bus_item):
            device.set_param(self.bus_item, self.busid)
        for i in xrange(len(self.addr_items)):
            if device.get_param(self.addr_items[i]):
                device.set_param(self.addr_items[i], addr[i])

    def insert(self, device, strict_mode=False, force=False):
        """
        True - Success
        False - Incorrect addr/busid
        None - No free slot
        string - Force add passed, returned string is message of errors
        """
        err = ""
        if not self._check_bus(device):
            if force:
                err += "BusId, "
                device.set_param(self.bus_item, self.busid)
            else:
                return False
        addr_pattern = self._dev2addr(device)
        addr = self._get_free_slot(addr_pattern)
        if addr is None:
            if force:
                if None in addr_pattern:
                    err += "NoFreeSlot, "
                    # Use last valid address for inserting the device
                    addr = [(_ - 1) for _ in self.addr_lengths]
                    self._insert_used(device, self._addr2stor(addr))
                else:   # used slot
                    err += "UsedSlot, "
                    addr = addr_pattern  # It's fully specified addr
                    self._insert_used(device, self._addr2stor(addr))
            else:
                return None
        elif addr is False:
            if force:
                addr = addr_pattern
                err += "BadAddr(%s), " % addr
                self._insert_oor(device, self._addr2stor(addr))
            else:
                return False
        else:
            self._insert_good(device, self._addr2stor(addr))
        if strict_mode:     # Set full address in strict_mode
            self._set_device_props(device, addr)
        else:
            self._update_device_props(device, addr)
        if err:
            # Device was force added with errors
            err = ("Force adding device %s into %s (errors: %s)"
                   % (device, self, err[:-2]))
            return err
        return True

    def _insert_good(self, device, addr):
        self.bus[self._addr2stor(addr)] = device

    def _insert_oor(self, device, addr):
        if addr in self.badbus:
            i = 2
            while "%s(%dx)" % (addr, i) in self.badbus:
                i += 1
            addr = "%s(%dx)" % (addr, i)
        self.badbus[addr] = device

    def _insert_used(self, device, addr):
        i = 2
        while "%s(%dx)" % (addr, i) in self.badbus:
            i += 1
        self.badbus["%s(%dx)" % (addr, i)] = device

    def remove(self, device):
        if not self._remove_good(device):
            return self._remove_bad(device)
        return True

    def _remove_good(self, device):
        if device in self.bus.iteritems():
            remove = None
            for key, item in self.bus.iteritems():
                if item is device:
                    remove = key
                    break
            if remove:
                del(self.bus[remove])
                return True
        return False

    def _remove_bad(self, device):
        if device in self.badbus.iteritems():
            remove = None
            for key, item in self.badbus.iteritems():
                if item is device:
                    remove = key
                    break
            if remove:
                del(self.badbus[remove])
                return True
        return False


class QSCSIBus(QSparseBus):
    def __init__(self, busid, bus_type=None, addr_spec=None, aobject=None):
        if bus_type is None:
            bus_type = 'virtio-scsi-pci'
        if addr_spec is None:
            addr_spec = [['scsi_id', 'lun'], [255, 16383]]
        super(QSCSIBus, self).__init__('bus', addr_spec, busid, bus_type,
                                       aobject)


class QUSBBus(QDense1DBus):
    def __init__(self, length, busid, bus_type, aobject=None):
        # FIXME: For compatibility reasons keep the USB types uhci,ehci,...
        for bus in 'uhci ehci ohci xhci'.split():
            if bus in bus_type:
                bus_type = bus
                break
        super(QUSBBus, self).__init__('bus', 'port', length, busid, bus_type,
                                      aobject)


class QPCIBus(QDense1DBus):
    def __init__(self, busid, bus_type, aobject=None):
        super(QPCIBus, self).__init__('bus', 'addr', 32, busid, bus_type,
                                      aobject)

    def _addr2stor(self, addr):
        if addr is None:
            return None
        else:
            return hex(addr)

    def _dev2addr(self, device):
        addr = device.get_param(self.addr_item)
        if addr is None:
            return None
        else:
            return int(addr, 16)


class QAHCIBus(QDense1DBus):
    def __init__(self, busid, aobject=None):
        super(QAHCIBus, self).__init__('bus', 'port', 12, busid, 'ahci',
                                       aobject)

    def _update_device_props(self, device, addr):
        if device.get_param('bus'):
            device.set_param('bus', "%s.%s" % (self.busid, addr / 2))
        if device.get_param('unit'):
            device.set_param('unit', addr % 2)

    def _set_device_props(self, device, addr):
        device.set_param('bus', "%s.%s" % (self.busid, addr / 2))
        device.set_param('unit', addr % 2)

    def _check_bus(self, device):
        bus = device.get_param('bus')
        if isinstance(bus, str):
            bus = bus.rsplit('.', 1)
            if len(bus) == 2 and bus[0] != self.busid:  # aaa.3
                return False
            elif not bus[0].isdigit() and bus[0] != self.busid:     # aaa
                return False
        return True # None, 5, '3'

    def _dev2addr(self, device):
        unit = None
        port = None
        bus = device.get_param('bus')
        if isinstance(bus, str):
            if bus.isdigit():
                unit = int(bus)
            else:
                bus = bus.rsplit('.', 1)
                if len(bus) == 2 and bus[1].isdigit():
                    unit = int(bus[1])
        if isinstance(bus, int):
            unit = bus
        if device.get_param('unit'):
            port = int(device.get_param('unit'))
        return self._param2addr((unit, port))

    def _param2addr(self, param=(None, None)):
        bus, unit = param
        if bus is not None and unit is not None:
            return 2 * bus + unit
        elif bus is not None:
            return [2 * bus, 2 * bus + 1]
        elif unit is not None:
            return [2 * i + unit for i in xrange(6)]
        return None

    @staticmethod
    def _addr2stor(addr):
        if isinstance(addr, int):
            return "%s:%s" % (addr / 2, addr % 2)
        return addr


class DevContainer(object):
    def __init__(self, qemu_help, device_help, vm, strict_mode=False):
        self.__qemu_help = qemu_help
        self.__device_help = device_help
        self.vm = vm
        self.vmname = vm.name
        self.strict_mode = strict_mode
        self.__devices = []
        self.__buses = [QPCIBus('pci.0', 'pci')]
        self.images = QDevImages(self)
        self.usbs = QDevUsbs(self)

    def __getitem__(self, item):
        """
        @param item: autotest id or QObject-like object
        @return: First matching object defined in this QDevContainer
        @raise KeyError: In case no match was found
        """
        if isinstance(item, QBaseDevice):
            if item in self.__devices:
                return item
        elif item:
            for device in self.__devices:
                if device.get_aid() == item:
                    return device
        raise KeyError("Device %s is not in %s" % (item, self))

    def __delitem__(self, item):
        """
        Delete specified item from devices list
        @param item: autotest id or QObject-like object
        @raise KeyError: In case no match was found
        """
        self.__devices.remove(self[item])

    def __contains__(self, item):
        """
        Is specified item defined in current devices list?
        @param item: autotest id or QObject-like object
        @return: True - yes, False - no
        """
        if isinstance(item, QBaseDevice):
            if item in self.__devices:
                return True
        elif item:
            for device in self.__devices:
                if device.get_aid() == item:
                    return True
        return False

    def __repr__(self):
        return self.str_short()

    def __iter__(self):
        """
        Iterate over all defined devices.
        """
        return self.__devices.__iter__()

    def get(self, item):
        """
        @param item: autotest id or QObject-like object
        @return: First matching object defined in this QDevContainer or None
        """
        if item in self:
            return self[item]

    def get_by_qid(self, qid):
        """
        @param qid: qemu id
        @return: List of items with matching qemu id
        """
        ret = []
        if qid:
            for device in self:
                if device.get_qid() == qid:
                    ret.append(device)
        return ret

    def str_short(self):
        out = "Devices of %s: [" % self.vmname
        for device in self:
            out += "%s," % device
        if out[-1] == ',':
            out = out[:-1]
        return out + "]"

    def str_bus_short(self):
        out = "Buses of %s\n  " % self.vmname
        for bus in self.__buses:
            out += str(bus).replace('\n', '\n  ')
        return out[:-3]

    def str_bus_long(self):
        out = "Devices of %s:\n  " % self.vmname
        for bus in self.__buses:
            out += bus.str_long().replace('\n', '\n  ')
        return out[:-3]

    def __create_unique_aid(self, qid):
        """
        Creates unique autotest id name from given qid
        @param qid: Original qemu id
        @return: aid (the format is "$qid__%d")
        """
        if qid and qid not in self:
            return qid
        i = 0
        while "%s__%d" % (qid, i) in self:
            i += 1
        return "%s__%d" % (qid, i)

    def has_option(self, option):
        """
        @param option: Desired option
        @return: Is the desired option supported by current qemu?
        """
        return bool(re.search(r"^-%s(\s|$)" % option, self.__qemu_help,
                              re.MULTILINE))

    def has_device(self, device):
        """
        @param device: Desired device
        @return: Is the desired device supported by current qemu?
        """
        return bool(re.search(r'name "%s"' % device, self.__device_help,
                              re.MULTILINE))
    def get_buses(self, bus_spec):
        buses = []
        for bus in self.__buses:
            for key, value in bus_spec.iteritems():
                if not bus.__getattribute__(key) == value:
                    break
            else:
                buses.append(bus)
        return buses

    def get_first_free_bus(self, bus_spec, addr):
        buses = self.get_buses(bus_spec)
        for bus in buses:
            _ = bus._get_free_slot(addr)
            if _ is not None and _ is not False:
                return bus

    def insert(self, device, force=False):
        """
        1) get list of matching parent buses
        2) try to find matching bus+address gently
        3) if it fails and force is specified, force it first on full, than on
           bad buses or without parent bus at all
        4) insert(0, child bus) (this way we always start with the latest bus)
        5) append into self.devices
        True - PASS
        False - not inserted
        string - inserted with error (only when force=True)
        """
        def clean():
            for bus in _used_buses:
                bus.remove(device)
            for bus in _added_buses:
                self.__buses.remove(bus)
        err = ""
        _used_buses = []
        _added_buses = []
        #1
        if device.parent_bus and not isinstance(device.parent_bus,
                                                (list, tuple)):
            # it have to be list of parent buses
            device.parent_bus = (device.parent_bus,)
        for parent_bus in device.parent_bus:
            # type, aobject, busid
            buses = self.get_buses(parent_bus)
            if not buses:
                if force:
                    err += "ParentBus(%s): No matching bus\n" % parent_bus
                    continue
                else:
                    clean()
                    return False
            bus_returns = []
            for bus in buses:   #2
                bus_returns.append(bus.insert(device, self.strict_mode, False))
                if bus_returns[-1] is True:     # we are done
                    _used_buses.append(bus)
                    break
            if bus_returns[-1] is True:
                continue
            elif not force:
                clean()
                return False
            if None in bus_returns: #3a
                _err = buses[bus_returns.index(None)].insert(device,
                                                    self.strict_mode, True)
                if _err:
                    err += "ParentBus(%s): %s\n" % (parent_bus, _err)
                    continue
            _err = buses[0].insert(device, self.strict_mode, True)
            _used_buses.append(bus)
            if _err:
                err += "ParentBus(%s): %s\n" % (parent_bus, _err)
                continue
        #4
        if device.child_bus and not isinstance(device.child_bus,
                                               (list, tuple)):
            # it have to be list of parent buses
            device.child_bus = (device.child_bus,)
        for bus in device.child_bus:
            self.__buses.insert(0, bus)
            _added_buses.append(bus)
        #5
        if device.get_qid() and self.get_by_qid(device.get_qid()):
            if not force:
                clean()
                return False
            else:
                err += "Devices qid %s already used in VM\n" % device.get_qid()
        device._set_aid(self.__create_unique_aid(device.get_qid()))
        self.__devices.append(device)
        if err:
            return ("Errors occured while adding device %s into %s:\n%s"
                    % (device, self, err))
        return True

    def list_missing_named_buses(self, bus_pattern, bus_type, bus_count):
        if not "%s" in bus_pattern:
            bus_pattern = bus_pattern + "%s"
        missing_buses = [bus_pattern % i for i in xrange(bus_count)]
        for bus in self.__buses:
            if bus.type == bus_type and re.match(bus_pattern % '\d+', bus.busid):
                if bus.busid in missing_buses:
                    missing_buses.remove(bus.busid)
        return missing_buses

    def idx_of_next_named_bus(self, bus_name):
        buses = []
        for bus in self.__buses:
            if bus.busid.startswith(bus_name):
                buses.append(bus.busid)
        i = 0
        while True:
            if bus_name + str(i) not in buses:
                return i
            i += 1

    def cmdline(self):
        """
        Creates cmdline arguments for creating all defined devices
        @return: cmdline of all devices (without qemu-cmd itself)
        """
        out = ""
        for device in self.__devices:
            if device.cmdline():
                out += " %s" % device.cmdline()
        return out

    def readconfig(self):
        """
        Creates -readconfig-like config for all defined devices.
        @return: list, where first item is -readconfig-like config for all
                 inserted devices. and second item are cmdline options of
                 devices, which don't have -readconfig fmt specified
        """
        out = ["", ""]
        for device in self.__devices:
            if device.readconfig():
                out[0] += "%s\n\n" % device.readconfig()
            elif device.cmdline():
                out[1] += "%s  "
        if out[0]:
            out[0] = out[0][:-2]
        if out[1]:
            out[1] = out[1][:-1]
        return out


if __name__ == "__main__":
    a = DevContainer(HELP, DEVICES, VM(), True)
    # -device ich9-usb-uhci
    """
    dev1 = QDevice(aobject='myusb1')
    dev1.set_param('driver', 'ich9-usb-uhci')
    dev1.set_param('addr', '0x7')
    dev1.parent_bus = {'type': 'pci'}
    dev1.child_bus = QUSBBus(6, 'myusb1.0', 'uhci', 'myusb1')
    #a.reserve_addr(dev1)    # might not work properly with strict_mode = False
    # finds the first free matching bus/port/addr and sets it to reserved
    # ...
    print a.insert(dev1)
    """
    devs = a.usbs.define_by_variables('myusb1', 'ich9-usb-uhci1', max_ports=2)
    for dev in devs:
        print "1: %s" % a.insert(dev)
    # 0) is VM running? is hotpluggable? ... etc.
    # 1) filter buses by parent_bus (aobject, type, busid)
    # 2) ask buses one by one for free port for this device, if none return err
    # 2a) bus looks for the 'addr' or 'unit' or whatever is necessarily and
    #     if it exists, look only for that particular place
    # 2b) if none, return err
    # 3) if strict_mode assign bus address (bus, lun, scsiid, ...) +modify dev1
    # 4) insert into bus(es) and devices.
    # -device usb-mouse
    dev2 = QDevice(aobject='mouse1')
    dev2.set_param('driver', 'usb-mouse')
    dev2.set_param('port', 1)
    #dev2.parent_bus = {'aobject': 'myusb1'}
    dev2.parent_bus = {'type': 'uhci'}
    print "2: %s" % a.insert(dev2)
    """
    # -device ahci,id=ahci
    dev3 = QDevice()
    dev3.child_bus = QAHCIBus('ahci1')
    dev3.parent_bus = {'type': 'pci'}
    dev3.set_param('driver', 'ahci')
    dev3.set_param('id', 'ahci1')
    print "3: %s" % a.insert(dev3)
    # -drive file=/tmp/aaa,id=bbb,if=none
    dev4 = QDrive(aobject='stg1')
    dev4.set_param('file', '/tmp/aaa')
    dev4.set_param('if', 'none')
    print "4: %s" % a.insert(dev4)
    # -device ide-drive,bus=ahci1.3,drive=drive_stg1,unit=1
    dev5 = QDevice(aobject='stg1')
    dev5.set_param('bus', 'ahci1.3')
    #dev5.set_param('drive', 'drive_stg1')
    dev5.set_param('unit', 1)
    dev5.parent_bus = ({'type': '__QDrive', 'aobject': 'stg1'}, {'type': 'ahci'})
    print "5: %s" % a.insert(dev5)
    """
    devs = a.images.define_by_variables('mydisk1', '/tmp/aaa', fmt='scsi-hd',
                                        cache='none', snapshot=True, bus=2,
                                        unit=4, port=1, bootindex=0)
    for dev in devs:
        print "3: %s" % a.insert(dev)
    devs = a.images.define_by_variables('mydisk2', '/tmp/bbb', fmt='scsi-hd',
                                        cache='none', snapshot=False, bus=2,
                                        unit=4, port=None, bootindex=1)
    for dev in devs:
        print "4: %s" % a.insert(dev)
    print "=" * 80
    print a.str_bus_long()
    print "=" * 80
    print a.cmdline()
    print "=" * 80
    print a.readconfig()[1]
    print a.readconfig()[0]
    while True:
        buf = raw_input()
        try:
            exec buf
        except Exception, inst:
            print "Exception: %s" % inst
