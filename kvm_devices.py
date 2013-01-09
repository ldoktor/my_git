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
        elif value:
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
                out += '%s< %4s >%s\n  ' % ('-' * 15, self._addr2str(addr),
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
                out += '%s< %4s >%s\n  ' % ('-' * 15, self._addr2str(addr),
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

    def _addr2str(self, addr):
        return addr

    def _str_bad_devices_long(self):
        out = ""
        if hasattr(self.badbus, 'iteritems'):
            for addr, dev in self.badbus.iteritems():
                out += '%s< %4s >%s\n  ' % ('-' * 15, self._addr2str(addr),
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
                out += '%s< %4s >%s\n  ' % ('-' * 15, self._addr2str(addr),
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
            out += "%s:" % self._addr2str(addr)
            out += "%s," % device
        if out[-1] == ',':
            out = out[:-1]
        return out + '}'

    def _addr2str(self, addr):
        if addr is None:
            return None
        else:
            return str(addr)

    def _param2addr(self, device):
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
        device.set_param(self.addr_item, self._addr2str(addr))

    def _update_device_props(self, device, addr):
        if device.get_param(self.bus_item):
            device.set_param(self.bus_item, self.busid)
        if device.get_param(self.addr_item):
            device.set_param(self.addr_item, self._addr2str(addr))

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
        _addr = self._param2addr(device)
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


class QUSBBus(QDense1DBus):
    def __init__(self, length, busid, bus_type, aobject=None):
        super(QUSBBus, self).__init__('bus', 'port', length, busid, bus_type,
                                      aobject)

class QPCIBus(QDense1DBus):
    def __init__(self, busid, bus_type, aobject=None):
        super(QPCIBus, self).__init__('bus', 'addr', 32, busid, bus_type,
                                      aobject)

    def _addr2str(self, addr):
        if addr is None:
            return None
        else:
            return hex(addr)

    def _param2addr(self, device):
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
        if (device.get_param('bus') and
                    device.get_param('bus').rsplit('.', 1)[0] != self.busid):
            return False
        else:
            return True

    def _param2addr(self, device):
        bus = None
        unit = None
        if device.get_param('bus'):
            bus = device.get_param('bus').rsplit('.', 1)
            if bus[-1].isdigit():
                bus = int(bus[-1])
        if device.get_param('unit'):
            unit = int(device.get_param('unit'))
        if bus is not None and unit is not None:
            return 2 * bus + unit
        elif bus is not None:
            return [2 * bus, 2 * bus + 1]
        elif unit is not None:
            return [2 * i + unit for i in xrange(6)]
        return None

    @staticmethod
    def _addr2str(addr):
        if isinstance(addr, int):
            return "%s:%s" % (addr / 2, addr % 2)
        return addr


"""
# Incomplete...
class QCustomBus(QBaseBus):
    #Bus defined by dictionarry with name and range
    def __init__(self, busid, bus_type, params, aobject=None):
        super(QCustomBus, self).__init__(busid, bus_type, aobject)
        self.bus = {}
        self.badbus = []
        self._keys = params.keys()
        self._ranges = params.values()
        self._mods = []
        mod = 1
        for i in range(len(self._ranges)[::-1]):
            self._mods.append(mod)
            mod *= self._ranges[i]
        self._mods = self._mods[::-1]
        self._max = mod

    def dev2addr(self, device):
        return [device.get_param(key) for key in self._keys]

    def addr2params(self, addr):
        if not isinstance(addr, (tuple, list)):
            addr = (addr,)
        params = {}
        for i in xrange(-len(addr), 0):
            params[self._keys[i]] = addr[i]
        return params

    def get_free_slot(self, device):
        addr = self.dev2addr(device)
        if not self._is_valid_addr(addr):
            raise KeyError("Device %s sets incorrect addr for %s" % (device,
                                                                     self))
        _addr = []
        for i in xrange(len(addr)):
            if addr[i] is None:
                _addr.append(range(0, self._ranges[i] * self._mods[i],
                                   self._mods[i]))
            # TODO...
                _addr.append(addr[i])
        while addr < self.mod:
            variables = []
            for i in xrange(len(addr)):
                if addr[i] is None:
                    variables.append(i)
                    addr[i] = 0
            for i in variables:
                for j in xrange(self._ranges[i]):
                    pass
                    # TODO

    def _is_valid_addr(self, addr):
        for i in xrange(-len(addr), 0):
            if addr[i] is None:
                continue
            if addr[i] >= self._ranges[i] or addr[i] < 0:
                return False
        return True

    def _insert(self, device, addr):
        if not self._is_valid_addr(addr) or addr in self.bus:
            self.badbus.append((addr, device))
"""


class DevContainer(object):
    def __init__(self, qemu_help, device_help, vm, strict_mode=False):
        self.__qemu_help = qemu_help
        self.__device_help = device_help
        self.vm = vm
        self.vmname = vm.name
        self.strict_mode = strict_mode
        self.__devices = []
        self.__buses = [QPCIBus('pci.0', 'pci')]
        #self.images = QDevImages(self)
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
    def get_system_bus(self):
        pass
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
            buses = []
            for bus in self.__buses:
                for key, value in parent_bus.iteritems():
                    if not bus.__getattribute__(key) == value:
                        break
                else:
                    buses.append(bus)
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


if __name__ == "__main__":
    a = DevContainer(HELP, DEVICES, VM())
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
    devs = a.usbs.define_by_variables('myusb1', 'ich9-usb-ehci1')
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
    dev2.set_param('port', 2)
    dev2.parent_bus = {'aobject': 'myusb1'}
    print "2: %s" % a.insert(dev2)
    # -device ahci,id=ahci
    dev3 = QDevice()
    dev3.child_bus = QAHCIBus('ahci1')
    dev3.parent_bus = {'type': 'pci'}
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
    print a.str_bus_long()
    while True:
        buf = raw_input()
        try:
            exec buf
        except Exception, inst:
            print "Exception: %s" % inst
