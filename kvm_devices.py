"""
Pre-draft version of new kvm_vm creation.

The idea is to create representation of VM using devices and dummy devices
(for non-device parts). It should simulate actual KVM VM creation so we'd be
able to compare in test the alikeness of monitor output and VM representation.

[hotplug]
Another income is, that it will be object representation where we can add
and remove devices according to current VM state (hotplug, unplug). This
version doesn't do hotplug/unplug automatically and it requires to use
representation to check for hotplug support, execute monitor.cmd() command
and gather the output and update the representation. This should change in
the future and the remove() should detect whether VM is running and initiate
the hotplug/unplug commands, checks for the result and compare with expected
result. At least that's my vision...

[necessity of reboot / params mapping]
This representation should logically follow KVM devices structure plus store
informations from params, thus we should be able to compare params and running
VM more deeply (after hotplug, after image change, ...).

[qdev/qtree mapping]
We should be also able to match/find/compare devices representation with qemu
representation (qtree, qdev, ...). We should be able to list all Devices of
the certain type, id, feature...

[bus]
Another feature should be the parent-child awareness. Nowadays there are lots
of variables stored in VM just to keep track of USB hubs, HD HBAs, etc. I tried
to create an automatic structure where devices with parent_type are
automatically inserted into a related HBA and the device properties are
updated (2 ways - always force full address "strict_mode" and normal mode,
where only given parts are updated [we might not specify bus, but we want to
specify port]).

[pci vs. pcie]
This implementation should be prepared for pcie (by concept)

[complex devices helpers]
To create a simple chardev, we might just create new object and that's it.
Anyway in autotest more complex devices are usually created from a single
params. For example one image can create multiple HBAs, drive and device.
To be able to do this there should be helpers. In a future these helpers should
also implement checking mechanism. (merge of kvm_qtree and this)

[qemu versioning]
The helpers should check for device support and choose the right device version
for the actual VM.
There is also a possibility to create image class, which might automatically
choose the right representation, but from what I tested it would be more
complicated, than this way.

[possibilities]
We should be able to create any device even from test and plug it into the
running (hotplug) or turned-off VM (new cmdline).

[readconfig]
I'd like to start with the readconfig method as in more complex tests we often
interfere with the too long cmdline.


IMPORTANT:
Don't follow any info in the code, it's probably crapy and old. This is my
way of finding the right direction and it will definitelly completely change.

Anyway currently I'd like to redesign it according to this ideas:
1) Basic devices classes (like QDevice).
2) Device might contain bus(es) of a certain type, but when the device is
   plugged in, the bus extends the current type bus container, or new
   container will be created with the device's bus.
3) There will be a bus container which allows one to find free port or get
   the list of buses of certain type.
4) Device's addr will be defined not in dev['addr'], but in a separate
   variable. The addr will be assigned during device insert phase.
5) Set of autocreatable buses will be created. In case a device tries to be
   inserted and the bus is missing, it allows one to handle this situation
   (eg. virtio-scsi5 is missing, so it will add missing 0-5 buses and plug
   the disk into this bus).
"""

import re
import logging
from collections import OrderedDict

import os
from duplicity import static
from ImageStat import Stat


HELP = os.popen('qemu-kvm -h').read()
DEVICES = os.popen('qemu-kvm -device ? 2>&1').read()

def NoneOrInt(value):
    if not value:   # "", None, False
        return None
    elif value.isdigit():
        return int(value)
    else:
        raise TypeError("This parameter have to be number or none")

class virt_vm:
    class VMDeviceNotSupportedError(Exception):
        pass


class VM:
    name = "vm1"


class DeviceError(Exception):
    pass


class VMDeviceError(Exception):
    pass


class VMDeviceStrError(VMDeviceError):
    def __init__(self, msg, vm=None, device=None, addr=None):
        self.msg = msg
        self.vm = vm
        self.addr = addr
        self.device = device

    def __str__(self):
        out = ""
        if self.vm:
            out += self.vm.str_long()
        if self.device:
            out += self.device.str_long()
        if self.addr:
            out += "%s\n" % self.addr
        return out + self.msg


class BusError(Exception):
    pass


class BusStrError(BusError):
    def __init__(self, msg, bus=None, device=None, addr=None):
        self.msg = msg
        self.bus = bus
        self.addr = addr
        self.device = device

    def __str__(self):
        return "%s (bus=%s, dev=%s, addr=%s)" % (self.msg, self.bus,
                                                 self.device, self.addr)


class BusOutOfRangeError(BusError):
    def __init__(self, bus, addr, device=None):
        self.bus = bus
        self.addr = addr
        self.device = device

    def __str__(self):
        msg = "Invalid address %s used" % self.addr
        if self.device:
            msg = " while handling device %s" % self.device
        msg += " in bus %s." % self.bus
        return msg


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
                                child_bus=Dense1DPortBus('%s.0' % usb_id,
                                                         'OLDVERSION_usb0',
                                                         max_ports))
            return [usb]

        if not self.qdev.has_device(usb_type):
                raise virt_vm.VMDeviceNotSupportedError(self.qdev.vmname,
                                                        usb_type)

        if pci_addr is None:
            _pci_addr = '* *'
        usb = QDevice({}, usb_id, 'pci', _pci_addr,
                       Dense1DPortBus('%s.0' % usb_id, usb_type, max_ports))
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
            usb.parent_type = None
            usb.set_param('addr', '1d.7')
            usb.set_param('multifunction', 'on')
            for i in xrange(3):
                new_usbs.append(QDevice())
                new_usbs[-1].set_param('id', '%s.%d' % (usb_id, i))
                new_usbs[-1].set_param('multifunction', 'on')
                new_usbs[-1].set_param('masterbus', '%s.0' % usb_id)
                new_usbs[-1].set_param('driver', 'ich9-usb-uhci%d' % i)
                new_usbs[-1].set_param('addr', '1d.%d' % i)
                new_usbs[-1].set_param('firstport', 2 * i)

        return new_usbs


class QDevImages(object):
    """
    Helper for defining images.
    @warning: In order to create indexes (HBAs, disk ids) properly you have to
              insert device into qdev before creating another disk device!
    """
    def __init__(self, qdev):
        self.qdev = qdev

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
        @param _media: type of the media (disk, cdrom, ...)
        @param _imgfmt: image format (qcow2, raw, ...)
        @param _pci_addr: drive pci address ($int)
        """
        # All related devices
        devices = []

        fmt = params.get("drive_format")
        supports_device = self.qdev.has_option("device")

        if strict_mode is None:
            strict_mode = self.qdev.strict_mode

        # TODO: Unify drive params
        bus = NoneOrInt(params.get("drive_bus", None)) # First level
        unit = NoneOrInt(params.get("drive_unit", None))   # Second level
        port = NoneOrInt(params.get("drive_port", None))   # Third level
        # Compatibility with old params - scsiid, lun
        if unit is None:
            unit = NoneOrInt(params.get("drive_scsiid", None))
        if port is None:
            port = NoneOrInt(params.get("drive_lun", None))
        """
        if isinstance(bus, str) and bus.isdigit():
            bus = int(bus)
        if isinstance(unit, str) and unit.isdigit():
            unit = int(unit)
        if isinstance(port, str) and port.isdigit():
            port = int(port)
        """

        # Create the full device address
        if fmt.startswith('scsi') or fmt in ['ahci']:
            addr = bus, unit, port
        else:
            addr = bus, unit

        """
        if params.get('drive_blkdebug') is not None:
            cmd = " -drive file=blkdebug:%s:%s" % (params.get('drive_blkdebug')
                                                   , params.get('image_name'))
        else:
            cmd = " -drive file='%s'" % params.get('image_name')
        """

        # fmt: ide, scsi, virtio, scsi-hd, ahci, usb1,2,3 + hba
        # device: ide-drive, usb-storage, scsi-hd, scsi-cd, virtio-blk-pci
        # bus: ahci, virtio-scsi-pci, USB

        if fmt == "ahci":
            for bus_name in self.qdev.bus.list_missing_buses('ahci', addr):
                dev = QDevice({'id': bus_name}, None, 'pci', None,
                              AHCIBus(bus_name, 'ahci', 6 * 2))
                devices.append(dev)
        else:
            #TODO: rest of fmt
            pass

        return devices








class QBaseDevice(object):
    """
    Base class for VMComponents objects
    """
    def __init__(self, dev_type="QBaseDevice", params={}, aobject=None,
                 parent_type=None, parent_addr=None, child_bus=None):
        """
        @param dev_type: type of this component
        @param params: component's parameters
        @param aobject: Autotest object which is associated with this device
        @param parent_type: type of the parent bus
        @param parent_addr: slot specification (in the parent_type format)
        @param child_bus: bus, which this device provides
        """
        self.aid = None
        self.type = dev_type
        self.aobject = aobject
        self.parent_type = parent_type
        self.parent_addr = parent_addr
        self.child_bus = child_bus
        self.params = {}
        for key, value in params.iteritems():
            self.set_param(key, value)

    def __setitem__(self, key, value):
        """ Default setter """
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
        elif value:
            self.params[option] = value
        elif value is None and option in self.params:
            del(self.params[option])

    def __getitem__(self, option):
        """ @return: object param """
        return self.params[option]

    def get(self, option):
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
        elif self.get_alternative_name():
            return self.get_alternative_name()
        else:
            return "t'%s'" % self.type

    def str_long(self):
        out = """%s
  aid = %s
  aobject = %s
  parent_type = %s
  parent_addr = %s
  child_bus = %s
  params:""" % (self.type, self.aid, self.aobject, self.parent_type,
                self.parent_addr, self.child_bus)
        for key, value in self.params.iteritems():
            out += "\n    %s = %s" % (key, value)
        return out + '\n'

    def get_alternative_name(self):
        """ @return: alternative object name """
        return None

    def get_qid(self):
        """ @return: qemu_id """
        return self.params.get('id', '')

    def get_aid(self):
        """ @return: autotest_id """
        return self.aid

    def set_aid(self, aid):
        """@param aid: new autotest id for this device"""
        self.aid = aid

    def cmdline(self):
        """
        @return: String which can be used on cmdline for creating this device
        """
        raise NotImplementedError

    def hotplug(self):
        """
        @return: String which can be used in monitor for hotplugging this dev
        """
        raise NotImplementedError

    def unplug(self):
        """
        @return: String which can be used in monitor for unplugging this device
        """
        raise NotImplementedError

    def readconfig(self):   # NotImplemented yet
        """
        @return: String which represents this device in -readconfig cfg format
        """
        raise NotImplementedError


class QStringDevice(QBaseDevice):
    """
    General device which allows to specify methods by fixed or parametrizable
    strings in this format:
      "%(type)s,id=%(id)s,addr=%(addr)s" -- params will be used to subst %()s
    """
    def __init__(self, dev_type=None, params={}, aobject=None,
                 parent_type=None, parent_addr=None, child_bus=None,
                 cmdline="", hotplug="", unplug="", readconfig=""):
        """
        @param dev_type: type of this component
        @param params: component's parameters
        @param aobject: Autotest object which is associated with this device
        @param parent_type: type of the parent bus
        @param parent_addr: slot specification (in the parent_type format)
        @param child_bus: bus, which this device provides
        @param cmdline: cmdline string
        @param hotplug: hotplug string
        @param unplug: unplug string
        @param readconfig: readconfig string
        """
        super(QStringDevice, self).__init__(dev_type, params, aobject,
                                            parent_type, parent_addr, child_bus)
        if dev_type:
            self.type = dev_type
        elif cmdline.split():
            self.type = "%s" % cmdline.split()[0]
        else:
            self.type = 'QStringDevice'
        self.__cmdline = cmdline
        self.__hotplug = hotplug
        self.__unplug = unplug
        self.__readconfig = readconfig

    def cmdline(self):
        try:
            return self.__cmdline % self.params
        except KeyError, details:
            raise KeyError("Param %s required for cmdline is not present in %s"
                           % (details, self.str_long()))

    def hotplug(self):
        try:
            return self.__hotplug % self.params
        except KeyError, details:
            raise KeyError("Param %s required for hotplug is not present in %s"
                           % (details, self.str_long()))

    def unplug(self):
        try:
            return self.__unplug % self.params
        except KeyError, details:
            raise KeyError("Param %s required for unplug is not present in %s"
                           % (details, self.str_long()))

    def readconfig(self):
        try:
            return self.__readconfig % self.params
        except KeyError, details:
            raise KeyError("Param %s required for readconfig is not present in"
                           " %s" % (details, self.str_long()))


class QCustomDevice(QBaseDevice):
    """
    Representation of the '-$option $param1=$value1,$param2...' qemu object.
    This representation handles only cmdline and readconfig outputs.
    """
    def __init__(self, dev_type, params={}, aobject=None,
                 parent_type=None, parent_addr=None, child_bus=None):
        """
        @param dev_type: The desired -$option parameter (device, chardev, ..)
        """
        super(QCustomDevice, self).__init__(dev_type, params, aobject,
                                            parent_type, parent_addr, child_bus)

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
    def __init__(self, params={}, aobject=None, parent_type=None,
                 parent_addr=None, child_bus=None):
        super(QDevice, self).__init__("device", params, aobject, parent_type,
                                      parent_addr, child_bus)

    def get_alternative_name(self):
        if self.params.get('driver'):
            return "d'%s'" % self.params.get('driver')

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
    def __init__(self, params={}, aobject=None, parent_type=None,
                 parent_addr=None, child_bus=None):
        super(QDevice, self).__init__("drive", params, aobject, parent_type,
                                      parent_addr, child_bus)

    def get_alternative_name(self):
        if self.params.get('if'):
            return "i'%s'" % self.params.get('if')

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


class BaseBus(object):
    """
    Base class for Bus representation objects
    """
    def __init__(self, busid, bus_type):
        self.busid = busid
        self.type = bus_type
        self.bus = None

    def __str__(self):
        return self.str_short()

    def str_short(self):
        return "%s(%s): %s  %s" % (self.busid, self.type, self._str_devices(),
                                   self._str_oor_devices())

    def str_long(self):
        return "Bus %s, type=%s\nSlots:\n%s\n%s" % (self.busid, self.type,
                    self._str_devices_long(), self._str_oor_devices_long())

    def _str_devices(self):
        return "[]"

    def _str_oor_devices(self):
        return "{}"

    def _str_devices_long(self):
        return "\n"

    def _str_oor_devices_long(self):
        return "\n"

    def _set_device_props(self, device, addr):
        raise NotImplemented

    def _update_device_props(self, device, addr):
        raise NotImplemented

    def get_free_slot(self, device=None, addr=None):
        raise NotImplemented

    def reserve(self, addr=None, force=False):
        return self.insert("reserved", addr, force)

    def _insert(self, device, addr=None, force=False):
        # Only inserts device into this bus
        raise NotImplemented

    def insert(self, device, addr=None, force=False):
        # Inserts device into this bus and sets device addr
        addr = self.get_free_slot(device, addr)
        addr = self._insert(device, addr, force)
        self._set_device_props(device, addr)
        return addr

    def remove(self, addr):
        raise NotImplemented


class Dense1DBus(BaseBus):
    """
    Represents bus with fixed number of slots. Address is defined by single
    integer. The output is optimized for small-densely used bus.
    """
    def __init__(self, busid, bus_type, length):
        """
        @param busid: Qemu bus id (usually qid followed by '.0')
        @param bus_type: Type of the bus
        @param length: bus length
        """
        super(Dense1DBus, self).__init__(busid, bus_type)
        self.bus = [None] * length      # Normal bus records
        self._bus = {}                  # Out-of-range bus records

    def _str_devices(self):
        out = "["
        for device in self.bus:
            if isinstance(device, list):
                for dev in device:
                    out += "%s|" % dev
                out = out[:-1] + ','
            else:
                out += "%s," % device
        if out[-1] == ',':
            out = out[:-1]
        return out + ']'

    def _str_oor_devices(self):
        out = '{'
        for addr, device in self._bus.iteritems():
            out += "%s:" % addr
            if isinstance(device, list):
                for dev in device:
                    out += "%s|" % dev
                out = out[:-1] + ','
            else:
                out += "%s," % device
        if out[-1] == ',':
            out = out[:-1]
        return out + '}'

    def _str_devices_long(self):
        out = ""
        for i in xrange(len(self.bus)):
            out += '%s< %4s >%s\n  ' % ('-' * 15, self._addr2str(i), '-' * 15)
            device = self.bus[i]
            if isinstance(device, list):
                for dev in device:
                    if isinstance(dev, str):
                        out += '"%s"\n  ' % dev
                    else:
                        out += dev.str_long().replace('\n', '\n  ')
                out = out[:-3]
            else:
                out += "%s" % device
            out += '\n'
        return out

    def _str_oor_devices_long(self):
        out = ""
        for addr, device in self._bus.iteritems():
            out += '%s< %4s >%s\n  ' % ('-' * 15, self._addr2str(addr),
                                        '-' * 15)
            if isinstance(device, list):
                for dev in device:
                    if isinstance(dev, str):
                        out += '"%s"\n  ' % dev
                    else:
                        out += dev.str_long().replace('\n', '\n  ')
                out = out[:-3]
            else:
                out += "%s" % device
            out += '\n'
        return out

    def _get_device_addr(self, device=None, addr=None):
        if addr is True and device:
            addr = device.get('addr')
        if isinstance(addr, str):
            addr = self._str2addr(addr)
        return addr

    @staticmethod
    def _str2addr(addr):
        if addr == '*':
            return None
        return int(addr)

    @staticmethod
    def _addr2str(addr):
        if addr:
            return str(addr)
        return addr

    def _set_device_props(self, device, addr):
        device.set_param('bus', self.busid)
        device.set_param('addr', self._addr2str(addr))

    def _update_device_props(self, device, addr):
        if device.get('bus'):
            device.set_param('bus', self.busid)
        if device.get('addr'):
            device.set_param('addr', self._addr2str(addr))

    def is_valid_addr(self, addr):
        if addr < len(self.bus) and addr >= -len(self.bus):
            return True
        return False

    def get_free_slot(self, device=None, addr=None):
        addr = self._get_device_addr(device, addr)
        if addr is not None:
            if addr < len(self.bus) and (self.bus[addr] == None or
                                         self.bus[addr] == "reserved"):
                return addr
        else:
            for addr in xrange(len(self.bus)):
                if self.bus[addr] == None:
                    return addr
        return None

    def _insert(self, device, addr=None, force=False):
        # TODO: This is all just wrong, create Container and then review this
        # TODO: reserve on full slot followed by insert device will erase the
        #       previous devices
        err = ""
        if force:
            err_msg = "Inserting device even when those errors occurred ["
        else:
            err_msg = "Can't insert device into bus: "
        if addr is None:    # No free addr
            if addr is None:
                err += "full bus, "
                if not force:
                    raise BusStrError(err_msg + err[:-2], self, device, addr)
        if self.is_valid_addr(addr):     # address is in correct range
            if self.bus[addr] is None:  # correct range and free slot
                self.bus[addr] = [device]
            elif "reserved" in self.bus[addr]:
                self.bus[addr] = [device]
                logging.warn(BusStrError("Using reserved addr", self, device,
                                         addr))
            else:   # correct range, used slot
                err += "addr in use, "
                if not force:
                    raise BusStrError(err_msg + err[:-2], self, device, addr)
                self.bus[addr].append(device)
        else:       # out-of-range
            err += "addr out-of-range, "
            if not force:
                raise BusStrError(err_msg + err[:-2], self, device, addr)
            if addr not in self._bus:  # out-of-range and free
                self._bus[addr] = [device]
            elif "reserved" in self._bus.get(addr):  # out-of-range + reserved
                logging.warn(BusStrError("Using reserved addr", self, device,
                                         addr))
                self._bus[addr] = [device]
            else:   # out-of-range and not free
                err += "addr in use, "
                if not force:
                    raise BusStrError(err_msg + err[:-2], self, device, addr)
                self._bus[addr].append(device)
        if err:
            logging.error(BusStrError(err_msg + err[:-2] + '].', self, device,
                                      addr))
        return addr


class PciBus(Dense1DBus):
    @staticmethod
    def _str2addr(addr):
        if isinstance(addr, str):
            if addr == '*':
                return None
            if addr.isdigit():  # decimal
                return int(addr)
            else:
                return int(addr, 16)
        return addr

    @staticmethod
    def _addr2str(addr):
        if isinstance(addr, int):
            return hex(addr)
        return addr


class Dense1DPortBus(Dense1DBus):
    def _update_device_props(self, device, addr):
        if device.get('bus'):
            device.set_param('bus', self.busid)
        if device.get('port'):
            device.set_param('port', self._addr2str(addr))

    def _set_device_props(self, device, addr):
        device.set_param('bus', self.busid)
        device.set_param('port', self._addr2str(addr))


class AHCIBus(Dense1DBus):
    def _update_device_props(self, device, addr):
        if device.get('bus'):
            device.set_param('bus', "%s.%s" % (self.busid, self.addr / 2))
        if device.get('unit'):
            device.set_param('unit', self._addr2str(addr % 2))

    def _set_device_props(self, device, addr):
        device.set_param('bus', "%s.%s" % (self.busid, self.addr / 2))
        device.set_param('unit', addr % 2)

    @staticmethod
    def _addr2str(addr):
        if isinstance(addr, int):
            return "%s:%s" % (addr / 2, addr % 2)
        return addr


class BusContainer(list):
    def __getitem__(self, bus_type):
        ret = self.get(bus_type)
        if not ret:
            raise KeyError("No bus of the type %s in %s" % (bus_type, self))
        return ret

    def get(self, bus_type):
        ret = []
        for bus in self:
            if bus.type == bus_type:
                ret.append(bus)
        return ret

    def get_first(self, bus_type):
        for bus in self:
            if bus.type == bus_type:
                return bus

    def __contains__(self, bus_type):
        return bool(self.get_first(bus_type))

    @staticmethod
    def split_bus_addr(bus_type, addr):
        if addr is None:
            addr = (None, None)
        return addr

    @staticmethod
    def bus_prefix(bus_type):
        return bus_type.lower().replace('-', '_') + '%d'

    def get_free_bus(self, bus_type, addr=None):
        buses = self.get(bus_type)
        addr = self.split_bus_addr(bus_type, addr)
        if addr[0] is not None:
            addr[0] = self.bus_prefix(bus_type) % addr[0]
        for bus in buses:
            if addr[0] is not None and bus.busid != addr[0]:
                continue
            if bus.get_free_port(addr=addr[1]) is not None:
                return bus
        else:
            return None



    def list_missing_buses(self, bus_type, addr=None):
        # @warning: bus addr have to be int or None
        # @note: bus name will be bus_type.lower().replace('-', '_') + addr
        new_buses = []
        addr = self.split_bus_addr(bus_type, addr)  # 2/None
        prefix = self.bus_prefix(bus_type)
        if addr[0] is not None:   # Create N buses prefix$number
            buses = self.get(bus_type)
            for i in xrange(addr[0]):
                if (prefix % i) not in buses:
                    new_buses.append(prefix % i)
        else:
            bus = self.get_free_bus(bus_type, addr)
            if not bus:
                buses = self.get(bus_type)
                i = 0
                while True:
                    if (prefix % i) not in buses:
                        new_buses.append(prefix % i)
                        break
                    i += 1
        return new_buses

    # TODO: Do everything :-)


class QDevContainer(object):
    """
    Provides an overview of VM's devices. It supports self.$module modules
    for complex devices handling.
    """
    def __init__(self, qemu_help, device_help, vm, strict_mode=False):
        self.__qemu_help = qemu_help
        self.__device_help = device_help
        self.vm = vm
        self.vmname = vm.name
        self.strict_mode = strict_mode
        # All devices present in VM should be in this list
        self.__devices = []
        # If fixed PCI addr used, devices in PCI bus should be here too
        #self.bus = {'pci.0': Dense1NBus("System", 'pci.0', 32)}
        self.bus = BusContainer([PciBus('pci.0', 'pci', 32)])

        # Helpers related to params objects
        self.images = QDevImages(self)
        self.usbs = QDevUsbs(self)
        #nics
        #...

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
        for bus in self.bus:
            out += str(bus).replace('\n', '\n  ')
        return out[:-3]

    def str_bus_long(self):
        out = "Devices of %s:\n  " % self.vmname
        for bus in self.bus:
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

    def get_system_bus(self):
        """
        Get main system bus representation.
        @note: In case fixed address are not used it will be completely free
        @return: main system bus representation
        """
        return self.bus['pci']

    def insert(self, device, force=False):
        """
        Adds device into devices representation.
        @warning: This only changes internal representation of devices.
        @param device: QObject-like device
        @param force: Force add even when qid already exists. Internally it
                      assignes new name for it, but on cmdline/hotplug it
                      will use the original id (generally for negative testing)
        @return: autotest id (usually the same as qemu id)
        @raise DeviceError: In case it can't be added (running VM,
                             existing id, incorrect data)
        """
        # TODO: handling of force
        err = ""
        if force:
            err_msg = "Inserting device even when those errors occured ["
        else:
            err_msg = "Can't insert device into VM: "
        if not isinstance(device, QBaseDevice):
            raise DeviceError("Not a QBaseDevice-like object (%s)" % device)
        if self.get_by_qid(device.get_qid()):   # qid already exists
            err += "duplicate qid, "
            if not force:
                raise VMDeviceStrError(err_msg + err[:-2], self, device)
        device.set_aid(self.__create_unique_aid(device.get_qid()))

        if device.parent_type:  # Device should be plugged into a bus
            if device.parent_type not in self.bus:  # Missing bus
                err += "missing bus"
                if self.strict_mode and not force:
                    raise VMDeviceStrError(err_msg + err, self, device)
            else:   # Plug into the right bus
                buses = self.bus[device.parent_type]
                des_bus = device.parent_addr   # None/string
                des_slot = None
                if des_bus is not None:
                    des_bus, des_slot = des_bus.split(' ', 1)  # "$bus $slot.."
                if des_bus == '*':  # '*' == any des_slot
                    des_bus = None
                if des_bus is not None:
                    if des_bus.isdigit():   # 0, 1, 2, 3, ...
                        # default name is bus_type + index + .0
                        des_bus = (device.parent_type.replace('-', '_')
                                   + des_bus + '.0')
                for bus in buses:
                    if des_bus is not None and bus.busid != des_bus:
                        continue
                    slot = bus.get_free_slot(device, des_slot)
                    if slot is not None:
                        slot = bus._insert(device, slot)
                    else:
                        continue    # try to plug into another bus
                    if self.strict_mode:
                        bus._set_device_props(device, slot)
                    else:
                        bus._update_device_props(device, slot)
                    break
                else:
                    # TODO: If force, force plug it into first matching bus
                    raise NotImplementedError

        if device.child_bus:    # Device provides a bus, add it to
            if isinstance(device.child_bus, BaseBus):
                self.bus.append(device.child_bus)
            else:
                self.bus.extend(device.child_bus)

        self.__devices.append(device)
        return device.get_aid()


if __name__ == "__main__":
    a = QDevContainer(HELP, DEVICES, VM())
    q = QDevice(({'driver': 'virtserialport', 'id': 'vio0'}))
    a.insert(q)
    print a.str_bus_long()
    q = QDevice({'driver': 'virtserialport', 'id': 'vio1'}, None, 'pci')
    a.insert(q)
    print a.str_bus_long()
    q = QDevice({'driver': 'virtserialport', 'id': 'vio2', 'bus': 'pcibus'},
                None, 'pci', '* 10')
    a.insert(q)
    print a.str_bus_long()
    q = QDevice({'driver': 'virtserialport', 'id': 'vio3', 'addr': True},
                None, 'pci', 'pci.0 *')
    a.insert(q)
    print a.str_bus_long()

    q = a.usbs.define_by_variables('moje_usb1', 'ich9-usb-ehci1')
    for dev in q:
        a.insert(dev)
    q = a.usbs.define_by_variables('ehci1', 'usb-ehci')
    a.insert(q[0])
    q = a.usbs.define_by_variables('ehci2', 'usb-ehci')
    a.insert(q[0])
    print a.str_bus_long()

    print "ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ"
    print "ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ"
    print "ZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZZ"
    q = a.images.define_by_params({'drive_format':'ahci'})
    for dev in q:
        a.insert(dev)
    print a.str_bus_long()
    while True:
        buf = raw_input()
        try:
            exec buf
        except Exception, inst:
            print "Exception: %s" % inst

"""
1) get_all buses
2) bus.get_free_slot()
3a) bus.insert(dev, addr)

3b) no free bus
4b) if force:
5b) bus[i].insert(dev, addr, force) -> last free position or addr

5c) raise exception or add bus (if not strict)


TODO: Don't use string, use tuple/array instead (None, 3, ...)
def.addr = None -> any bus any slot
dev.addr = "moje_usb1.0 3" -> bus qid = moje_usb1.0, slot 3
dev.addr = "* *" -> any bus on any slot
dev.addr = "* 3" -> any bus, slot 3
dev.addr = "3 *" -> 3rd bus with default qid of this type (usb_ehci3.0),
                       any slot

dev.bustype = usb-ehci, pci, virtio-scsi-pci, ...
dev.bustype = None -> don't assign bus

dev[bus] = pci.0, usb_ehci3.0, virtio_scsi_pci7.0, ide.1

* strict mode
** always update all dev[]
** if anything is None, try to find free bus/slot. If it fails, raise an error
** order is:
    1) on line
    2) dev.addr

* nonstrict mode
** if bus/addr1/addrX not None, update dev[]
** if anything is None, try to find free bus/slot. If it fails, add new bus and
   update only those things, which are not None
** order is:
    1) on line
    2) dev.addr

0) if addr specified on line, use only this one, set dev[bus], dev[addr]
1) dev.bustype -> find matching buses
2) dev.addr -> split into bus-addr1..addrn, find matching, if possible insert
               or add new bus (if bus=*)
2b) if dev[addr] -> update it, if dev[bus] -> update it, ...
3) elif dev[bus] -> logging.warn(bus specified, but no dev.bustype)
4) elif dev.addr -> logging.warn(

1) dev[bus] -> find desired bus, if not found, create one
1) dev[addr] -> request this addr
2) dev[bus] -> insert into bus or error
3) dev.addr -> update addr
4) dev.bus -> force bus
"""


































