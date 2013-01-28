"""
Goals:
[During vm.__qemu_cmdline()]
  1) creates vm.devices representation
  2) calls vm.devices.cmdline()  (or .readconfig())
  - it could automatically assign full address (addr, scsiid, ports, ...)
  - it could check, whether there are any problems with current configuration

[In test]
  - we could access the vm.devices representation
    - all used params should be there
    - bus representation
    - verification (supersedes kvm_qtree)

  - we could hotplug new devices:
    dev = QDevice({'driver':'usb-mouse'},aobject='mouse1')
    vm.monitor(dev.hotplug())     # + check the output
    vm.devices.insert(dev)

  - we could unplug devices:
    dev = vm.devices.get('device_specification')
    vm.monitor(dev.unplug())    # + check the output
    vm.devices.remove(dev)

  - we could see the devices in buses (short output)
    Buses of vm1
      drive_mydisk2(QDrive): mydisk2  {}
      drive_mydisk1(QDrive): mydisk1  {}
      virtio_scsi_pci2.0(virtio-scsi-pci): {4-0:mydisk2,4-1:mydisk1}  {}
      virtio_scsi_pci1.0(virtio-scsi-pci): {}  {}
      virtio_scsi_pci0.0(virtio-scsi-pci): {}  {}
      myusb1.0(uhci): [None,a'usb-mouse']  {}
      pci.0(pci): [myusb1,virtio_scsi_pci0.0,virtio_scsi_pci1.0,\
                   virtio_scsi_pci2.0,None,None,None,None,None,None,None,None,\
                   None,None,None,None,None,None,None,None,None,None,None,None,\
                   None,None,None,None,None,None,None,None]  {}
    - In the long output you can see the devices params etc.


[After the test]
  - We could compare actual state of the VM (hotplug/unplug/drivechange...)


...
[Testing]
import sys
sys.path.append('/home/medic/Work/AAA/autotest/tmp/my_git')
from kvm_devices import *

del(sys.modules['kvm_devices'])
from kvm_devices import *
"""

import os
import re
import logging


HELP = os.popen('qemu-kvm -h').read()
DEVICES = os.popen('qemu-kvm -device ? 2>&1').read()


def none_or_int(value):
    """ Helper fction which returns None or int() """
    if not value:   # "", None, False
        return None
    elif isinstance(value, str) and value.isdigit():
        return int(value)
    elif isinstance(value, int):
        return value
    else:
        raise TypeError("This parameter have to be number or none")


# Fake classes
class virt_vm(object):
    class VMDeviceNotSupportedError(Exception):
        pass


class VM(object):
    def __init__(self, name="vm1"):
        self.name = name


class storage(object):
    @staticmethod
    def get_image_filename(image_params, root_dir=None):
        root_dir = root_dir
        return image_params.get("image_filename")

    @staticmethod
    def get_image_blkdebug_filename(image_params, root_dir=None):
        root_dir = root_dir
        return image_params.get("drive_blkdebug")
# End of the fake classes


class DeviceError(Exception):
    """ General device exception """
    pass


class QDevUsbs(object):
    """
    Helper class for handling USB buses.
    @warning: Devices have to be plugged directly after definition, otherwise
              the assigned addresses might not be valid!
    """
    def __init__(self, qdev):
        """
        @param qdev: Parent device container
        """
        self.qdev = qdev

    def define_by_variables(self, usb_id, usb_type, multifunction=False,
                            masterbus=None, firstport=None, freq=None,
                            max_ports=None, pci_addr=None):
        """
        Creates related devices by variables
        @param usb_id: Usb bus name
        @param usb_type: Usb bus type
        @param multifunction: Is the bus multifunction
        @param masterbus: Is this bus master?
        @param firstport: Offset of the first port
        @param freq: Bus frequency
        @param max_ports: How many ports this bus have [6]
        @param pci_addr: Desired PCI address
        """
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
        usb.set_param('multifunction', multifunction)
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
        """
        Wrapper for creating usb bus from autotest usb params.
        @param usb_name: Name of the usb bus
        @param params: USB params (params.object_params(usb_name))
        """
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
        """
        @param qdev: Parent device container
        """
        self.qdev = qdev

    def _define_hbas(self, hba, bus, unit, port, qbus):
        """
        Helper for creating HBAs of certain type.
        """
        devices = []
        if qbus == QAHCIBus:    # AHCI uses multiple ports, id is different
            _hba = 'ahci%s'
        else:
            _hba = hba.replace('-', '_') + '%s.0'  # HBA id
        _bus = bus
        if bus is None:
            bus = self.qdev.get_first_free_bus({'type': hba},
                                               [unit, port])
            if bus is None:
                bus = self.qdev.idx_of_next_named_bus(_hba)
            else:
                bus = bus.busid
        if isinstance(bus, int):
            for bus_name in self.qdev.list_missing_named_buses(
                                        _hba, hba, bus + 1):
                # TODO: Make list of ranges of various scsi_hbas.
                #       This is based on virtio-scsi-pci
                devices.append(QDevice({'id': bus_name, 'driver': hba}
                                       , None, {'type': 'pci'},
                                       qbus(bus_name)))
            bus = _hba % bus
        if qbus == QAHCIBus and unit is not None:
            bus += ".%d" % unit
        return devices, bus, {'type': hba}

    def define_by_variables(self, name, filename, index=None, fmt=None,
                      cache=None, werror=None, rerror=None, serial=None,
                      snapshot=None, boot=None, blkdebug=None, bus=None,
                      unit=None, port=None, bootindex=None, removable=None,
                      min_io_size=None, opt_io_size=None,
                      physical_block_size=None, logical_block_size=None,
                      readonly=None, scsiid=None, lun=None, aio=None,
                      strict_mode=None, media=None, imgfmt=None,
                      pci_addr=None, scsi_hba=None):
        """
        Creates related devices by variables
        @note: To skip the argument use None, to disable it use False
        @note: Strictly bool options accept "yes", "on" and True ("no"...)
        @param name: Autotest name of this disk
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
        @param bus: 1st level of disk location (index of bus) ($int)
        @param unit: 2nd level of disk location (unit/scsiid/...) ($int)
        @param port: 3rd level of disk location (port/lun/...) ($int)
        @param bootindex: device boot priority ($int)
        @param removable: can the drive be removed? ($bool)
        @param min_io_size: Min allowed io size
        @param opt_io_size: Optimal io size
        @param physical_block_size: set physical_block_size ($int)
        @param logical_block_size: set logical_block_size ($int)
        @param readonly: set the drive readonly ($bool)
        @param scsiid: Deprecated 2nd level of disk location (&unit)
        @param lun: Deprecated 3rd level of disk location (&port)
        @param aio: set the type of async IO (native, threads, ..)
        @param strict_mode: enforce optional parameters (address, ...) ($bool)
        @param media: type of the media (disk, cdrom, ...)
        @param imgfmt: image format (qcow2, raw, ...)
        @param pci_addr: drive pci address ($int)
        @param scsi_hba: Custom scsi HBA
        """
        # All related devices
        devices = []

        supports_device = self.qdev.has_option("device")
        if fmt == "scsi":   # fmt=scsi force the old version of devices
            logging.warn("'scsi' drive_format is deprecated, please use the "
                         "new lsi_scsi type for disk %s", name)
            supports_device = False

        if strict_mode is None:
            strict_mode = self.qdev.strict_mode
        if strict_mode:
            if cache is None:
                cache = "none"
            if removable is None:
                removable = "yes"
            if aio is None:
                aio = "native"
            if media is None:
                media = "disk"

        # TODO: Unify drive params
        bus = none_or_int(bus)     # First level
        unit = none_or_int(unit)   # Second level
        port = none_or_int(port)   # Third level
        # Compatibility with old params - scsiid, lun
        if unit is None:
            if scsiid is not None:
                logging.warn("drive_scsiid param of disk %s is deprecated, use"
                             " drive_unit instead", name)
            unit = none_or_int(scsiid)
        if port is None:
            if lun is not None:
                logging.warn("drive_lun param of disk %s is deprecated, use "
                             " drive_port instead", name)
            port = none_or_int(lun)

        # fmt: ide, scsi, virtio, scsi-hd, ahci, usb1,2,3 + hba
        # device: ide-drive, usb-storage, scsi-hd, scsi-cd, virtio-blk-pci
        # bus: ahci, virtio-scsi-pci, USB

        # HBA
        if not supports_device:
            # TODO: Add bus representation as it's added automatically
            # if scsi: when not free add next  (scsi)
            pass
        elif fmt == "ide":
            if bus:
                logging.warn('ide supports only 1 hba, use drive_unit to set'
                             'ide.* for disk %s', name)
            bus = unit
            dev_parent = {'type': 'ide'}
        elif fmt == "ahci":
            _, bus, dev_parent = self._define_hbas('ahci', bus, unit, port,
                                                   QAHCIBus)
            devices.extend(_)
        elif fmt.startswith('scsi-'):
            # TODO: When lun is None use 0 instead as it's not used by qemu arg
            # parser to assign luns (when there is no place it incr scsiid
            # in non strict_mode (strict_mode can assign any scsiid+lun
            if not scsi_hba:
                scsi_hba = "virtio-scsi-pci"
            _, bus, dev_parent = self._define_hbas(scsi_hba, bus, unit, port,
                                                   QSCSIBus)
            devices.extend(_)
        elif fmt in ('usb1', 'usb2', 'usb3'):
            if bus:
                logging.warn('Manual setting of drive_bus is not yet supported'
                             ' for usb disk %s', name)
                bus = None
            if fmt == 'usb1':
                dev_parent = {'type': 'uhci'}
            elif fmt == 'usb2':
                dev_parent = {'type': 'ehci'}
            elif fmt == 'usb3':
                dev_parent = {'type': 'xhci'}
        else:
            dev_parent = {'type': fmt}
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
            if fmt.startswith('scsi-') and scsi_hba == 'lsi53c895a':
                fmt = 'scsi'  # Compatibility with the new scsi
            if fmt not in ('ide', 'scsi', 'sd', 'mtd', 'floppy', 'pflash',
                           'virtio'):
                raise virt_vm.VMDeviceNotSupportedError(self.qdev.vmname,
                                                        fmt)
            devices[-1].set_param('if', fmt)    # overwrite previously set None
            devices[-1].set_param('index', index)
            # TODO: Add floppy when supported
            if fmt in ('ide', 'scsi', 'floppy'):  # Don't handle sd, pflash...
                devices[-1].parent_bus += ({'type': fmt},)
            if fmt == 'virtio':
                devices[-1].set_param('addr', pci_addr)
                devices[-1].parent_bus += ({'type': 'pci'},)
            return devices

        # Device
        devices.append(QDevice({}, name))
        devices[-1].parent_bus += ({'busid': 'drive_%s' % name}, dev_parent)
        devices[-1].set_param('id', name)
        devices[-1].set_param('bus', bus)
        devices[-1].set_param('drive', 'drive_%s' % name)
        devices[-1].set_param('logical_block_size', logical_block_size)
        devices[-1].set_param('physical_block_size', physical_block_size)
        devices[-1].set_param('min_io_size', min_io_size)
        devices[-1].set_param('opt_io_size', opt_io_size)
        devices[-1].set_param('bootindex', bootindex)
        if fmt != 'virtio':
            devices[-1].set_param('serial', serial)
            devices[-1].set_param('removable', removable)
        if fmt in ("ide", "ahci"):
            devices[-1].set_param('driver', 'ide-drive')
            devices[-1].set_param('unit', port)
        elif fmt.startswith('scsi-'):
            devices[-1].set_param('driver', fmt)
            devices[-1].set_param('scsi_id', unit)
            devices[-1].set_param('lun', port)
            if strict_mode:
                devices[-1].set_param('channel', 0)
        elif fmt in ('usb1', 'usb2', 'usb3'):
            devices[-1].set_param('driver', 'usb-storage')
            devices[-1].set_param('port', unit)
        elif fmt == 'floppy':
            # Overwrite QDevice with QFloppy
            devices[-1] = QFloppy(unit, 'drive_%s' % name, name,
                                ({'busid': 'drive_%s' % name}, {'type': fmt}))

        return devices

    def define_by_params(self, name, image_params, media=None, root_dir=None):
        """
        Wrapper for creating disks and related hbas from autotest image params.
        @note: To skip the argument use None, to disable it use False
        @note: Strictly bool options accept "yes", "on" and True ("no"...)
        @note: Options starting with '_' are optional and used only when
               strict_mode == True
        @param name: Name of the new disk
        @param params: Disk params (params.object_params(name))
        """
        # TODO: Index, index_in_use, ...
        return self.define_by_variables(name,
                  storage.get_image_filename(image_params, root_dir),
                  image_params.get("drive_index"),
                  image_params.get("drive_format"),
                  image_params.get("drive_cache"),
                  image_params.get("drive_werror"),
                  image_params.get("drive_rerror"),
                  image_params.get("drive_serial"),
                  image_params.get("image_snapshot"),
                  image_params.get("image_boot"),
                  storage.get_image_blkdebug_filename(image_params, root_dir),
                  image_params.get("drive_bus"),
                  image_params.get("drive_unit"),
                  image_params.get("drive_port"),
                  image_params.get("bootindex"),
                  image_params.get("removable"),
                  image_params.get("min_io_size"),
                  image_params.get("opt_io_size"),
                  image_params.get("physical_block_size"),
                  image_params.get("logical_block_size"),
                  image_params.get("image_readonly"),
                  image_params.get("drive_scsiid"),
                  image_params.get("drive_lun"),
                  image_params.get("image_aio"),
                  image_params.get("strict_mode"),
                  media,
                  image_params.get("image_format"),
                  image_params.get("drive_pci_addr"),
                  image_params.get("scsi_hba"))


class QBaseDevice(object):
    """ Base class of qemu objects """
    def __init__(self, dev_type="QBaseDevice", params=None, aobject=None,
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
        if params:
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

    def __getitem__(self, option):
        """ @return: object param """
        return self.params[option]

    def __delitem__(self, option):
        """ deletes self.params[option] """
        del(self.params[option])

    def __len__(self):
        """ length of self.params """
        return len(self.params)

    def __setitem__(self, option, value):
        """ self.set_param(option, value, None) """
        return self.set_param(option, value)

    def __contains__(self, option):
        """ Is the option set? """
        return option in self.params

    def __str__(self):
        """ Short string representation """
        return self.str_short()

    def str_short(self):
        """ Short representation (aid, qid, alternative, type) """
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
        """ Full representation, multi-line with all params """
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

    def set_aid(self, aid):
        """@param aid: new autotest id for this device"""
        self.aid = aid

    def cmdline(self):
        """ @return: cmdline command to define this device """
        raise NotImplementedError

    def hotplug(self):
        """ @return: monitor command to hotplug this device """
        raise DeviceError("Hotplug is not supported by this device %s", self)

    def unplug(self):
        """ @return: monitor command to unplug this device """
        raise DeviceError("Unplug is not supported by this device %s", self)

    def readconfig(self):
        """ @return: readconfig-like config of this device """
        raise NotImplementedError


class QStringDevice(QBaseDevice):
    """
    General device which allows to specify methods by fixed or parametrizable
    strings in this format:
      "%(type)s,id=%(id)s,addr=%(addr)s" -- params will be used to subst %()s
    """
    def __init__(self, dev_type, params=None, aobject=None,
                 parent_bus=(), child_bus=(), cmdline="", hotplug="",
                 unplug="", readconfig=""):
        """
        @param dev_type: type of this component
        @param params: component's parameters
        @param aobject: Autotest object which is associated with this device
        @param parent_bus: bus(es), in which this device is plugged in
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
        """ @return: cmdline command to define this device """
        try:
            return self._cmdline % self.params
        except KeyError, details:
            raise KeyError("Param %s required for cmdline is not present in %s"
                           % (details, self.str_long()))

    def hotplug(self):
        """ @return: monitor command to hotplug this device """
        try:
            return self._hotplug % self.params
        except KeyError, details:
            raise KeyError("Param %s required for hotplug is not present in %s"
                           % (details, self.str_long()))

    def unplug(self):
        """ @return: monitor command to unplug this device """
        try:
            return self._unplug % self.params
        except KeyError, details:
            raise KeyError("Param %s required for unplug is not present in %s"
                           % (details, self.str_long()))

    def readconfig(self):
        """ @return: readconfig-like config of this device """
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
    def __init__(self, dev_type, params=None, aobject=None,
                 parent_bus=(), child_bus=()):
        """
        @param dev_type: The desired -$option parameter (device, chardev, ..)
        """
        super(QCustomDevice, self).__init__(dev_type, params, aobject,
                                            parent_bus, child_bus)

    def cmdline(self):
        """ @return: cmdline command to define this device """
        out = "-%s " % self.type
        for key, value in self.params.iteritems():
            out += "%s=%s," % (key, value)
        if out[-1] == ',':
            out = out[:-1]
        return out

    def readconfig(self):
        """ @return: readconfig-like config of this device """
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
    def __init__(self, params=None, aobject=None, parent_bus=(),
                 child_bus=()):
        super(QDevice, self).__init__("device", params, aobject, parent_bus,
                                      child_bus)

    def _get_alternative_name(self):
        """ @return: alternative object name """
        if self.params.get('driver'):
            return self.params.get('driver')

    def hotplug(self):
        """ @return: monitor command to hotplug this device """
        out = "device_add "
        for key, value in self.params.iteritems():
            out += "%s=%s," % (key, value)
        if out[-1] == ',':
            out = out[:-1]
        return out

    def unplug(self):
        """ @return: monitor command to unplug this device """
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
        """
        Set device param using qemu notation ("on", "off" instead of bool...)
        It restricts setting of the 'id' param as it's automatically created.
        @param option: which option's value to set
        @param value: new value
        @param option_type: type of the option (bool)
        """
        if option == 'id':
            raise KeyError("Drive ID is automatically created from aobject. %s"
                           % self)
        super(QDrive, self).set_param(option, value, option_type)

    def hotplug(self):
        """ @return: monitor command to hotplug this device """
        out = "drive_add auto "
        for key, value in self.params.iteritems():
            out += "%s=%s," % (key, value)
        if out[-1] == ',':
            out = out[:-1]
        return out

    def unplug(self):
        """ @return: monitor command to unplug this device """
        if self.get_qid():
            return "drive_del %s" % self.get_qid()


class QGlobal(QBaseDevice):
    """
    Representation of qemu global setting (-global driver.property=value)
    """
    def __init__(self, driver, prop, value, aobject=None,
                 parent_bus=(), child_bus=()):
        """
        @param driver: Which global driver to set
        @param prop: Which property to set
        @param value: What's the desired value
        @param params: component's parameters
        @param aobject: Autotest object which is associated with this device
        @param parent_bus: bus(es), in which this device is plugged in
        @param child_bus: bus, which this device provides
        """
        params = {'driver': driver, 'property': prop, 'value': value}
        super(QGlobal, self).__init__('global', params, aobject,
                                      parent_bus, child_bus)

    def cmdline(self):
        return "-global %s.%s=%s" % (self['driver'], self['property'],
                                     self['value'])

    def readconfig(self):
        return ('[global]\n  driver = "%s"\n  property = "%s"\n  value = "%s"'
                '\n' % (self['driver'], self['property'], self['value']))


# TODO: Use None instead of () for parent_bus and child_bus
class QFloppy(QGlobal):
    """
    Imitation of qemu floppy disk defined by -global isa-fdc.drive?=$drive
    """
    def __init__(self, unit=None, drive=None, aobject=None, parent_bus=(),
                 child_bus=()):
        """
        @param unit: Floppy unit (None, 0, 1 or driveA, driveB)
        @param drive: id of drive
        @param aobject: Autotest object which is associated with this device
        @param parent_bus: bus(es), in which this device is plugged in
        @param child_bus: bus(es), which this device provides
        """
        super(QFloppy, self).__init__('isa-fdc', unit, drive, aobject,
                                      parent_bus, child_bus)

    def _get_alternative_name(self):
        return "floppy-%s" % (self.get_param('property'))

    def set_param(self, option, value, option_type=None):
        """
        drive and unit params have to be 'translated' as value and property.
        """
        if option == 'drive':
            option = 'value'
        elif option == 'unit':
            option = 'property'
        super(QFloppy, self).set_param(option, value, option_type)


class QSparseBus(object):
    """
    Universal bus representation object.
    It inserts devices into bus (good bus) or in case of bad addr into badbus
    dictionaries on the respective addr.
    adresses:
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

    def __str__(self):
        """ default string representation """
        return self.str_short()

    def str_short(self):
        """ short string representation """
        return "%s(%s): %s  %s" % (self.busid, self.type, self._str_devices(),
                                   self._str_bad_devices())

    def _str_devices(self):
        """ short string representation of the good bus """
        out = '{'
        for addr in sorted(self.bus.keys()):
            out += "%s:" % addr
            out += "%s," % self.bus[addr]
        if out[-1] == ',':
            out = out[:-1]
        return out + '}'

    def _str_bad_devices(self):
        """ short string representation of the bad bus """
        out = '{'
        for addr in sorted(self.badbus.keys()):
            out += "%s:" % addr
            out += "%s," % self.badbus[addr]
        if out[-1] == ',':
            out = out[:-1]
        return out + '}'

    def str_long(self):
        """ long string representation """
        return "Bus %s, type=%s\nSlots:\n%s\n%s" % (self.busid, self.type,
                    self._str_devices_long(), self._str_bad_devices_long())

    def _str_devices_long(self):
        """ long string representation of devices in the good bus """
        out = ""
        for addr, dev in self.bus.iteritems():
            out += '%s< %4s >%s\n  ' % ('-' * 15, addr,
                                        '-' * 15)
            if isinstance(dev, str):
                out += '"%s"\n  ' % dev
            else:
                out += dev.str_long().replace('\n', '\n  ')
                out = out[:-3]
            out += '\n'
        return out

    def _str_bad_devices_long(self):
        """ long string representation of devices in the bad bus """
        out = ""
        for addr, dev in self.badbus.iteritems():
            out += '%s< %4s >%s\n  ' % ('-' * 15, addr,
                                        '-' * 15)
            if isinstance(dev, str):
                out += '"%s"\n  ' % dev
            else:
                out += dev.str_long().replace('\n', '\n  ')
                out = out[:-3]
            out += '\n'
        return out

    def _increment_addr(self, addr, last_addr=None):
        """
        Increment addr base of addr_pattern and last used addr
        @param addr: addr_pattern
        @param last_addr: previous address
        @return: last_addr + 1
        """
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

    @staticmethod
    def _addr2stor(addr):
        """
        Converts internal addr to storable/hashable address
        @param addr: internal address [addr1, addr2, ...]
        @return: storable address "addr1-addr2-..."
        """
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
        """
        Parse the internal address out of the device
        @param device: QBaseDevice device
        @return: internal address  [addr1, addr2, ...]
        """
        addr = []
        for key in self.addr_items:
            addr.append(device.get_param(key))
        return addr

    def get_free_slot(self, addr_pattern):
        """
        Finds unoccupied address
        @param addr_pattern: Address pattern (full qualified or with Nones)
        @return: First free address when found, (free or reserved for this dev)
                 None when no free address is found, (all occupied)
                 False in case of incorrect address (oor)
        """
        # init
        use_reserved = True
        if addr_pattern is None:
            addr_pattern = [None] * len(self.addr_lengths)
        # set first usable addr
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
        # Increment addr until free match is found
        while last_addr is not False:
            if self._addr2stor(last_addr) not in self.bus:
                return last_addr
            if (use_reserved and
                        self.bus[self._addr2stor(last_addr)] == "reserved"):
                return last_addr
            last_addr = self._increment_addr(addr_pattern, last_addr)
        return None     # No free matching address found

    def _check_bus(self, device):
        """
        Check, whether this device can be plugged into this bus.
        @param device: QBaseDevice device
        @return: True in case ids are correct, False when not
        """
        if (device.get_param(self.bus_item) and
                    device.get_param(self.bus_item) != self.busid):
            return False
        else:
            return True

    def _set_device_props(self, device, addr):
        """
        Set the full device address
        @param device: QBaseDevice device
        @param addr: internal address  [addr1, addr2, ...]
        """
        device.set_param(self.bus_item, self.busid)
        for i in xrange(len(self.addr_items)):
            device.set_param(self.addr_items[i], addr[i])

    def _update_device_props(self, device, addr):
        """
        Update values of previously set address items.
        @param device: QBaseDevice device
        @param addr: internal address  [addr1, addr2, ...]
        """
        if device.get_param(self.bus_item):
            device.set_param(self.bus_item, self.busid)
        for i in xrange(len(self.addr_items)):
            if device.get_param(self.addr_items[i]):
                device.set_param(self.addr_items[i], addr[i])

    def insert(self, device, strict_mode=False, force=False):
        """
        Insert device into this bus representation.
        @param device: QBaseDevice device
        @param strict_mode: Use strict mode (set optional params)
        @param force: Force insert the device even when errs occurs
        @return: True on success,
                 False when an incorrect addr/busid is set,
                 None when there is no free slot,
                 error string when force added device with errors.
        """
        err = ""
        if not self._check_bus(device):
            if force:
                err += "BusId, "
                device.set_param(self.bus_item, self.busid)
            else:
                return False
        addr_pattern = self._dev2addr(device)
        addr = self.get_free_slot(addr_pattern)
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
        """
        Insert device into good bus
        @param device: QBaseDevice device
        @param addr: internal address  [addr1, addr2, ...]
        """
        self.bus[addr] = device

    def _insert_oor(self, device, addr):
        """
        Insert device into bad bus as out-of-range (o)
        @param device: QBaseDevice device
        @param addr: storable address "addr1-addr2-..."
        """
        addr = "o" + addr
        if addr in self.badbus:
            i = 2
            while "%s(%dx)" % (addr, i) in self.badbus:
                i += 1
            addr = "%s(%dx)" % (addr, i)
        self.badbus[addr] = device

    def _insert_used(self, device, addr):
        """
        Insert device into bad bus because address is already used
        @param device: QBaseDevice device
        @param addr: storable address "addr1-addr2-..."
        """
        i = 2
        while "%s(%dx)" % (addr, i) in self.badbus:
            i += 1
        self.badbus["%s(%dx)" % (addr, i)] = device

    def remove(self, device):
        """
        Remove device from this bus
        @param device: QBaseDevice device
        @return: True when removed, False when the device wasn't found
        """
        if not self._remove_good(device):
            return self._remove_bad(device)
        return True

    def _remove_good(self, device):
        """
        Remove device from the good bus
        @param device: QBaseDevice device
        @return: True when removed, False when the device wasn't found
        """
        if device in self.bus.itervalues():
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
        """
        Remove device from the bad bus
        @param device: QBaseDevice device
        @return: True when removed, False when the device wasn't found
        """
        if device in self.badbus.itervalues():
            remove = None
            for key, item in self.badbus.iteritems():
                if item is device:
                    remove = key
                    break
            if remove:
                del(self.badbus[remove])
                return True
        return False


class QDriveBus(QSparseBus):
    """
    QDrive bus representation (single slot, drive=...)
    """
    def __init__(self, busid, aobject=None):
        """
        @param busid: id of the bus (pci.0)
        @param aobject: Related autotest object (image1)
        """
        super(QDriveBus, self).__init__('drive', [[], []], busid, 'QDrive',
                                        aobject)

    def get_free_slot(self, addr_pattern):
        """ Use only drive as slot """
        if 'drive' in self.bus:
            return None
        else:
            return True

    @staticmethod
    def _addr2stor(addr):
        """ address is always drive """
        return 'drive'

    def _update_device_props(self, device, addr):
        """ Always set -drive property, it's mandatory """
        self._set_device_props(device, addr)


class QDenseBus(QSparseBus):
    """
    Dense bus representation. The only difference from SparseBus is the output
    string format. DenseBus iterates over all addresses and show free slots
    too. SparseBus on the other hand prints always the device address.
    """
    def _str_devices_long(self):
        """ Show all addresses even when they are unused """
        out = ""
        addr_pattern = [None] * len(self.addr_items)
        addr = [0] * len(self.addr_items)
        while addr:
            dev = self.bus.get(self._addr2stor(addr))
            out += '%s< %4s >%s\n  ' % ('-' * 15, self._addr2stor(addr),
                                        '-' * 15)
            if hasattr(dev, 'str_long'):
                out += dev.str_long().replace('\n', '\n  ')
                out = out[:-3]
            elif isinstance(dev, str):
                out += '"%s"' % dev
            else:
                out += "%s" % dev
            out += '\n'
            addr = self._increment_addr(addr_pattern, addr)
        return out

    def _str_bad_devices_long(self):
        """ Show all addresses even when they are unused """
        out = ""
        for addr, dev in self.badbus.iteritems():
            out += '%s< %4s >%s\n  ' % ('-' * 15, addr,
                                        '-' * 15)
            if isinstance(dev, str):
                out += '"%s"\n  ' % dev
            else:
                out += dev.str_long().replace('\n', '\n  ')
                out = out[:-3]
            out += '\n'
        return out

    def _str_devices(self):
        """ Show all addresses even when they are unused, don't print addr """
        out = '['
        addr_pattern = [None] * len(self.addr_items)
        addr = [0] * len(self.addr_items)
        while addr:
            out += "%s," % self.bus.get(self._addr2stor(addr))
            addr = self._increment_addr(addr_pattern, addr)
        if out[-1] == ',':
            out = out[:-1]
        return out + ']'

    def _str_bad_devices(self):
        """ Show all addresses even when they are unused """
        out = '{'
        for addr in sorted(self.badbus.keys()):
            out += "%s:" % addr
            out += "%s," % self.badbus[addr]
        if out[-1] == ',':
            out = out[:-1]
        return out + '}'


class QSCSIBus(QSparseBus):
    """
    SCSI bus representation (bus + 2 leves, don't iterate over lun by default)
    """
    def __init__(self, busid, bus_type=None, addr_spec=None, aobject=None):
        """
        @param busid: id of the bus (mybus.0)
        @param bus_type: type of the bus (virtio-scsi-pci, lsi53c895a, ...)
        @param aobject: Related autotest object (image1)
        """
        if bus_type is None:
            bus_type = 'virtio-scsi-pci'
        if addr_spec is None:
            addr_spec = [['scsi_id', 'lun'], [256, 16384]]
        super(QSCSIBus, self).__init__('bus', addr_spec, busid, bus_type,
                                       aobject)

    def _dev2addr(self, device):
        """
        Qemu doesn't increment lun automatically so don't use it when
        it's not explicitelly specified.
        """
        addr = []
        for key in self.addr_items:
            if (key == 'lun' and device.get_param(key) is None):
                addr.append(0)  # Luns are not assigned and by default are 0
            else:
                addr.append(device.get_param(key))
        return addr


class QUSBBus(QDenseBus):
    """
    USB bus representation. (bus&port, hubs are not supported)
    """
    def __init__(self, length, busid, bus_type, aobject=None):
        """
        Bus type have to be generalized and parsed from original bus type:
        (usb-ehci == ehci, ich9-usb-uhci1 == uhci, ...)
        """
        # FIXME: For compatibility reasons keep the USB types uhci,ehci,...
        for bus in ('uhci', 'ehci', 'ohci', 'xhci'):
            if bus in bus_type:
                bus_type = bus
                break
        super(QUSBBus, self).__init__('bus', [['port'], [length]], busid,
                                      bus_type, aobject)


class QPCIBus(QDenseBus):
    """
    PCI Bus representation (bus&addr, uses hex digits)
    """
    def __init__(self, busid, bus_type, aobject=None):
        """ bus&addr, 32 slots """
        super(QPCIBus, self).__init__('bus', [['addr'], [32]], busid, bus_type,
                                      aobject)

    @staticmethod
    def _addr2stor(addr):
        """ force all items as hexadecimal values """
        out = ""
        for value in addr:
            if value is None:
                out += '*-'
            else:
                out += '%s-' % hex(value)
        if out:
            return out[:-1]
        else:
            return "*"

    def _dev2addr(self, device):
        """ Read the values in base of 16 (hex) """
        addr = []
        for key in self.addr_items:
            value = device.get_param(key)
            if value is None:
                addr.append(None)
            else:
                addr.append(int(value, 16))
        return addr


class QBusUnitBus(QDenseBus):
    """ Implementation of bus-unit bus (ahci, ide) """
    def __init__(self, busid, bus_type, lengths, aobject=None):
        """
        @param busid: id of the bus (mybus.0)
        @param bus_type: type of the bus (ahci)
        @param lenghts: lenghts of [buses, units]
        @param aobject: Related autotest object (image1)
        """
        if len(lengths) != 2:
            raise ValueError("len(lenghts) have to be 2 (%s)" % self)
        super(QBusUnitBus, self).__init__('bus', [['bus', 'unit'], lengths],
                                          busid, bus_type, aobject)

    def _update_device_props(self, device, addr):
        """ This bus is compound of m-buses + n-units, update properties """
        if device.get_param('bus'):
            device.set_param('bus', "%s.%s" % (self.busid, addr[0]))
        if device.get_param('unit'):
            device.set_param('unit', addr[1])

    def _set_device_props(self, device, addr):
        """This bus is compound of m-buses + n-units, set properties """
        device.set_param('bus', "%s.%s" % (self.busid, addr[0]))
        device.set_param('unit', addr[1])

    def _check_bus(self, device):
        """ This bus is compound of m-buses + n-units, check correct busid """
        bus = device.get_param('bus')
        if isinstance(bus, str):
            bus = bus.rsplit('.', 1)
            if len(bus) == 2 and bus[0] != self.busid:  # aaa.3
                return False
            elif not bus[0].isdigit() and bus[0] != self.busid:     # aaa
                return False
        return True # None, 5, '3'

    def _dev2addr(self, device):
        """ This bus is compound of m-buses + n-units, parse addr from dev """
        bus = None
        unit = None
        busid = device.get_param('bus')
        if isinstance(busid, str):
            if busid.isdigit():
                bus = int(busid)
            else:
                busid = busid.rsplit('.', 1)
                if len(busid) == 2 and busid[1].isdigit():
                    bus = int(busid[1])
        if isinstance(busid, int):
            bus = busid
        if device.get_param('unit'):
            unit = int(device.get_param('unit'))
        return [bus, unit]



class QAHCIBus(QBusUnitBus):
    """ AHCI bus (ich9-ahci, ahci) """
    def __init__(self, busid, aobject=None):
        """ 6xbus, 2xunit """
        super(QAHCIBus, self).__init__(busid, 'ahci', [6, 2], aobject)


class QIDEBus(QBusUnitBus):
    """ IDE bus (piix3-ide) """
    def __init__(self, busid, aobject=None):
        """ 2xbus, 2xunit """
        super(QIDEBus, self).__init__(busid, 'ide', [2, 2], aobject)


class QFloppyBus(QDenseBus):
    """
    Floppy bus (-global isa-fdc.drive?=$drive)
    """
    def __init__(self, busid, aobject=None):
        """ property <= [driveA, driveB] """
        super(QFloppyBus, self).__init__(None, [['property'], [2]], busid,
                                         'floppy', aobject)

    @staticmethod
    def _addr2stor(addr):
        """ translate as drive$CHAR """
        return "drive%s" % chr(65 + addr[0]) # 'A' + addr

    def _dev2addr(self, device):
        """ Read None, number or drive$CHAR and convert to int() """
        addr = device.get_param('property')
        if isinstance(addr, str):
            if addr.startswith('drive') and len(addr) > 5:
                addr = ord(addr[5])
            elif addr.isdigit():
                addr = int(addr)
        return [addr]

    def _update_device_props(self, device, addr):
        """ Always set props """
        self._set_device_props(device, addr)

    def _set_device_props(self, device, addr):
        """ Change value to drive{A,B,...} """
        device.set_param('property', self._addr2stor(addr))


class DevContainer(object):
    """
    Device container class
    """
    def __init__(self, qemu_help, device_help, vm, strict_mode=False):
        """
        @param qemu_help: output of qemu -h
        @param device_help: output of qemu -device ?
        @param vm: related VM
        @param strict_mode: Use strict mode (set optional params)
        """
        self.__qemu_help = qemu_help
        self.__device_help = device_help
        self.vm = vm
        self.vmname = vm.name
        self.strict_mode = strict_mode
        self.__devices = []
        self.__buses = [QPCIBus('pci.0', 'pci')]
        # Autotest subsystem helpers (usbs, images, ...)
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

    def __len__(self):
        """ @return: Number of inserted devices """
        return len(self.__devices)

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

    def __iter__(self):
        """ Iterate over all defined devices. """
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
        """ Short string representation of all devices """
        out = "Devices of %s: [" % self.vmname
        for device in self:
            out += "%s," % device
        if out[-1] == ',':
            out = out[:-1]
        return out + "]"

    def str_bus_short(self):
        """ Short representation of all buses """
        out = "Buses of %s\n  " % self.vmname
        for bus in self.__buses:
            out += str(bus)
            out += "\n  "
        return out[:-3]

    def str_bus_long(self):
        """ Long representation of all buses """
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
        """
        @param bus_spec: Bus specification (dictionary)
        @return: All matching buses
        """
        buses = []
        for bus in self.__buses:
            for key, value in bus_spec.iteritems():
                if not bus.__getattribute__(key) == value:
                    break
            else:
                buses.append(bus)
        return buses

    def get_first_free_bus(self, bus_spec, addr):
        """
        @param bus_spec: Bus specification (dictionary)
        @param addr: Desired address
        @return: First matching bus with free desired address (the latest
                 added matching bus)
        """
        buses = self.get_buses(bus_spec)
        for bus in buses:
            _ = bus.get_free_slot(addr)
            if _ is not None and _ is not False:
                return bus

    def insert(self, device, force=False):
        """
        Inserts device into this VM representation
        @param device: QBaseDevice device
        @param force: Force insert the device even when errs occurs
        @return: True on success,
                 False when error occurs,
                 error string when force added device with errors.

        1) get list of matching parent buses
        2) try to find matching bus+address gently
        3) if it fails and force is specified, force it first on full, than on
           bad buses or without parent bus at all
        4) insert(0, child bus) (this way we always start with the latest bus)
        5) append into self.devices
        """
        def clean():
            """ Remove all inserted devices on failure """
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
            _used_buses.append(buses[0])
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
        device.set_aid(self.__create_unique_aid(device.get_qid()))
        self.__devices.append(device)
        if err:
            return ("Errors occured while adding device %s into %s:\n%s"
                    % (device, self, err))
        return True

    def list_missing_named_buses(self, bus_pattern, bus_type, bus_count):
        """
        @param bus_pattern: Bus name pattern with 1x%s for idx or %s is
                            appended in the end. ('mybuses' or 'my%sbus').
        @param bus_type: Type of the bus.
        @param bus_count: Desired number of buses.
        @return: List of buses, which are missing in range(bus_count)
        """
        if not "%s" in bus_pattern:
            bus_pattern = bus_pattern + "%s"
        missing_buses = [bus_pattern % i for i in xrange(bus_count)]
        for bus in self.__buses:
            if bus.type == bus_type and re.match(bus_pattern % '\d+',
                                                 bus.busid):
                if bus.busid in missing_buses:
                    missing_buses.remove(bus.busid)
        return missing_buses

    def idx_of_next_named_bus(self, bus_pattern):
        """
        @param bus_pattern: Bus name prefix without %s and tailing digit
        @return: Name of the next bus (integer is appended and incremented
                 until there is no existing bus).
        """
        buses = []
        for bus in self.__buses:
            if bus.busid.startswith(bus_pattern):
                buses.append(bus.busid)
        i = 0
        while True:
            if bus_pattern + str(i) not in buses:
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
                out[1] += "%s  " % device.cmdline()
        if out[0]:
            out[0] = out[0][:-2]
        if out[1]:
            out[1] = out[1][:-1]
        return out


if __name__ == "__main__":
    a = DevContainer(HELP, DEVICES, VM(), False)
    # Add default devices
    a.insert(QStringDevice('qemu', cmdline='qemu-kvm'))
    a.insert(QStringDevice('ide', child_bus=QIDEBus('ide')))  # ide bus
    a.insert(QStringDevice('fdc', child_bus=QFloppyBus('floppy')))  # floppyBus
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
    for dev1 in devs:
        print "1: %s" % a.insert(dev1)
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
    dev5.parent_bus = ({'type': 'QDrive', 'aobject': 'stg1'}, {'type': 'ahci'})
    print "5: %s" % a.insert(dev5)
    """
    devs = a.images.define_by_variables('mydisk1', '/tmp/aaa', fmt='ahci',
                                        cache='none', snapshot=True, bus=0,
                                        unit=1, port=1, bootindex=0)
    for dev1 in devs:
        print "3: %s" % a.insert(dev1, force=True)
    devs = a.images.define_by_variables('mydisk2', '/tmp/bbb', fmt='ahci',
                                        cache='none', snapshot=False, bus=None,
                                        unit=None, port=None, bootindex=1)
    for dev1 in devs:
        print "4: %s" % a.insert(dev1)
    print "=" * 80
    print a.str_bus_long()
    print "=" * 80
    print a.cmdline()
    print "=" * 80
    print "# %s" % a.readconfig()[1]
    print a.readconfig()[0]
    print a.str_bus_short()
    while False:
        buf = raw_input()
        try:
            exec buf
        except Exception, inst:
            print "Exception: %s" % inst
