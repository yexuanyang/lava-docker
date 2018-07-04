#!/usr/bin/env python
#
from __future__ import print_function
import os, sys, time
import subprocess
import yaml
import string
import socket
import shutil

# Defaults
boards_yaml = "boards.yaml"
tokens_yaml = "tokens.yaml"
baud_default = 115200

template_conmux = string.Template("""#
# auto-generated by lavalab-gen.py for ${board}
#
listener ${board}
application console '${board} console' 'exec sg dialout "cu-loop /dev/${board} ${baud}"'
""")

#no comment it is volontary
template_device = string.Template("""{% extends '${devicetype}.jinja2' %}
""")

template_device_conmux = string.Template("""
{% set connection_command = 'conmux-console ${board}' %}
""")
template_device_connection_command = string.Template("""#
{% set connection_command = '${connection_command}' %}
""")
template_device_pdu_generic = string.Template("""
{% set hard_reset_command = '${hard_reset_command}' %}
{% set power_off_command = '${power_off_command}' %}
{% set power_on_command = '${power_on_command}' %}
""")

template_ser2net = string.Template("""
${port}:telnet:600:/dev/${board}:${baud} 8DATABITS NONE 1STOPBIT banner
""")
template_device_ser2net = string.Template("""
{% set connection_command = 'telnet 127.0.0.1 ${port}' %}
""")

template_device_screen = string.Template("""
{% set connection_command = 'ssh -o StrictHostKeyChecking=no -t root@127.0.0.1 "TERM=xterm screen -x ${board}"' %}
""")

template_udev_serial = string.Template("""#
SUBSYSTEM=="tty", ATTRS{idVendor}=="${idvendor}", ATTRS{idProduct}=="${idproduct}", ATTRS{serial}=="${serial}", MODE="0664", OWNER="uucp", SYMLINK+="${board}"
""")
template_udev_devpath = string.Template("""#
SUBSYSTEM=="tty", ATTRS{idVendor}=="${idvendor}", ATTRS{idProduct}=="${idproduct}", ATTRS{devpath}=="${devpath}", MODE="0664", OWNER="uucp", SYMLINK+="${board}"
""")

template_settings_conf = string.Template("""
{
    "DEBUG": false,
    "STATICFILES_DIRS": [
        ["lava-server", "/usr/share/pyshared/lava_server/htdocs/"]
    ],
    "MEDIA_ROOT": "/var/lib/lava-server/default/media",
    "ARCHIVE_ROOT": "/var/lib/lava-server/default/archive",
    "STATIC_ROOT": "/usr/share/lava-server/static",
    "STATIC_URL": "/static/",
    "MOUNT_POINT": "/",
    "HTTPS_XML_RPC": false,
    "LOGIN_URL": "/accounts/login/",
    "LOGIN_REDIRECT_URL": "/",
    "CSRF_COOKIE_SECURE": $cookie_secure,
    "SESSION_COOKIE_SECURE": $session_cookie_secure
}
""")

def main():
    need_zmq_auth_gen = False
    fp = open(boards_yaml, "r")
    workers = yaml.load(fp)
    fp.close()

    os.mkdir("output")
    zmq_auth_genlist = open("zmqauth/zmq_auth_gen/zmq_genlist", 'w')

    if "masters" not in workers:
        print("Missing masters entry in boards.yaml")
        sys.exit(1)
    masters = workers["masters"]
    for master in masters:
        keywords_master = [ "name", "type", "host", "users", "tokens", "webadmin_https", "persistent_db", "zmq_auth", "zmq_auth_key", "zmq_auth_key_secret" ]
        for keyword in master:
            if not keyword in keywords_master:
                print("WARNING: unknown keyword %s" % keyword)
        name = master["name"]
        print("Handle %s\n" % name)
        if not "host" in master:
            host = "local"
        else:
            host = master["host"]
        workerdir = "output/%s/%s" % (host, name)
        os.mkdir("output/%s" % host)
        shutil.copy("deploy.sh", "output/%s/" % host)
        dockcomp = {}
        dockcomp["version"] = "2.0"
        dockcomp["services"] = {}
        dockcomposeymlpath = "output/%s/docker-compose.yml" % host
        dockcomp["services"][name] = {}
        dockcomp["services"][name]["hostname"] = name
        dockcomp["services"][name]["ports"] = [ "10080:80", "5555:5555", "5556:5556", "5500:5500" ]
        dockcomp["services"][name]["volumes"] = [ "/boot:/boot", "/lib/modules:/lib/modules" ]
        dockcomp["services"][name]["build"] = {}
        dockcomp["services"][name]["build"]["context"] = name
        persistent_db = False
        if "persistent_db" in master:
            persistent_db = master["persistent_db"]
        if persistent_db:
            pg_volume_name = "pgdata_" + name
            dockcomp["services"][name]["volumes"].append(pg_volume_name + ":/var/lib/postgresql")
            dockcomp["services"][name]["volumes"].append("lava_job_output:/var/lib/lava-server/default/media/job-output/")
            dockcomp["volumes"] = {}
            dockcomp["volumes"][pg_volume_name] = {}
            dockcomp["volumes"]["lava_job_output"] = {}
        with open(dockcomposeymlpath, 'w') as f:
            yaml.dump(dockcomp, f)

        shutil.copytree("lava-master", workerdir)
        os.mkdir("%s/devices" % workerdir)
        # handle users / tokens
        userdir = "%s/users" % workerdir
        os.mkdir(userdir)
        worker = master
        webadmin_https = False
        if "webadmin_https" in worker:
            webadmin_https = worker["webadmin_https"]
        if webadmin_https:
            cookie_secure = "true"
            session_cookie_secure = "true"
        else:
            cookie_secure = "false"
            session_cookie_secure = "false"
        fsettings = open("%s/settings.conf" % workerdir, 'w')
        fsettings.write(template_settings_conf.substitute(cookie_secure=cookie_secure, session_cookie_secure=session_cookie_secure))
        fsettings.close()
        master_use_zmq_auth = False
        if "zmq_auth" in worker:
            master_use_zmq_auth = True
        if master_use_zmq_auth:
            if "zmq_auth_key" in worker:
                shutil.copy(worker["zmq_auth_key"], "%s/zmq_auth/" % workerdir)
                shutil.copy(worker["zmq_auth_key_secret"], "%s/zmq_auth/" % workerdir)
            else:
                zmq_auth_genlist.write("%s/%s\n" % (host, name))
                need_zmq_auth_gen = True
        if "users" in worker:
            for user in worker["users"]:
                keywords_users = [ "name", "staff", "superuser", "password", "token" ]
                for keyword in user:
                    if not keyword in keywords_users:
                        print("WARNING: unknown keyword %s" % keyword)
                username = user["name"]
                ftok = open("%s/%s" % (userdir, username), "w")
                token = user["token"]
                ftok.write("TOKEN=" + token + "\n")
                if "password" in user:
                    password = user["password"]
                    ftok.write("PASSWORD=" + password + "\n")
                    # libyaml convert yes/no to true/false...
                if "staff" in user:
                    value = user["staff"]
                    if value is True:
                        ftok.write("STAFF=1\n")
                if "superuser" in user:
                    value = user["superuser"]
                    if value is True:
                        ftok.write("SUPERUSER=1\n")
                ftok.close()
        tokendir = "%s/tokens" % workerdir
        os.mkdir(tokendir)
        if "tokens" in worker:
            filename_num = {}
            print("Found tokens")
            for token in worker["tokens"]:
                keywords_tokens = [ "username", "token", "description" ]
                for keyword in token:
                    if not keyword in keywords_tokens:
                        print("WARNING: unknown keyword %s" % keyword)
                username = token["username"]
                description = token["description"]
                if username in filename_num:
                    number = filename_num[username]
                    filename_num[username] = filename_num[username] + 1
                else:
                    filename_num[username] = 1
                    number = 0
                filename = "%s-%d" % (username, number)
                print("\tAdd token for %s in %s" % (username, filename))
                ftok = open("%s/%s" % (tokendir, filename), "w")
                ftok.write("USER=" + username + "\n")
                vtoken = token["token"]
                ftok.write("TOKEN=" + vtoken + "\n")
                ftok.write("DESCRIPTION=\"%s\"" % description)
                ftok.close()

    default_slave = "lab-slave-0"
    if "slaves" not in workers:
        print("Missing slaves entry in boards.yaml")
        sys.exit(1)
    slaves = workers["slaves"]
    for slave in slaves:
        keywords_slaves = [ "name", "host", "dispatcher_ip", "remote_user", "remote_master", "remote_address", "remote_rpc_port", "remote_proto", "extra_actions", "zmq_auth_key", "zmq_auth_key_secret" ]
        for keyword in slave:
            if not keyword in keywords_slaves:
                print("WARNING: unknown keyword %s" % keyword)
        name = slave["name"]
        if len(slaves) == 1:
            default_slave = name
        print("Handle %s" % name)
        if not "host" in slave:
            host = "local"
        else:
            host = slave["host"]
        if slave.get("default_slave") and slave["default_slave"]:
             default_slave = name
        workerdir = "output/%s/%s" % (host, name)
        dockcomposeymlpath = "output/%s/docker-compose.yml" % host
        if not os.path.isdir("output/%s" % host):
            os.mkdir("output/%s" % host)
            shutil.copy("deploy.sh", "output/%s/" % host)
            dockcomp = {}
            dockcomp["version"] = "2.0"
            dockcomp["services"] = {}
        else:
            #master exists
            fp = open(dockcomposeymlpath, "r")
            dockcomp = yaml.load(fp)
            fp.close()
        dockcomp["services"][name] = {}
        dockcomp["services"][name]["hostname"] = name
        dockcomp["services"][name]["dns_search"] = ""
        dockcomp["services"][name]["ports"] = [ "69:69/udp", "80:80", "61950-62000:61950-62000" ]
        dockcomp["services"][name]["volumes"] = [ "/boot:/boot", "/lib/modules:/lib/modules" ]
        dockcomp["services"][name]["environment"] = {}
        dockcomp["services"][name]["build"] = {}
        dockcomp["services"][name]["build"]["context"] = name
        # insert here remote

        shutil.copytree("lava-slave", workerdir)
        fp = open("%s/phyhostname" % workerdir, "w")
        fp.write(host)
        fp.close()
        conmuxpath = "%s/conmux" % workerdir
        if not os.path.isdir(conmuxpath):
            os.mkdir(conmuxpath)

        worker = slave
        worker_name = name
        #NOTE remote_master is on slave
        if not "remote_master" in worker:
            remote_master = "lava-master"
        else:
            remote_master = worker["remote_master"]
        if not "remote_address" in worker:
            remote_address = remote_master
        else:
            remote_address = worker["remote_address"]
        if not "remote_rpc_port" in worker:
            remote_rpc_port = "80"
        else:
            remote_rpc_port = worker["remote_rpc_port"]
        dockcomp["services"][worker_name]["environment"]["LAVA_MASTER"] = remote_address
        remote_user = worker["remote_user"]
        # find master
        remote_token = "BAD"
        for fm in workers["masters"]:
            if fm["name"] == remote_master:
                for fuser in fm["users"]:
                    if fuser["name"] == remote_user:
                        remote_token = fuser["token"]
                if "zmq_auth" in fm:
                    if "zmq_auth_key" in fm:
                        shutil.copy(fm["zmq_auth_key"], "%s/zmq_auth/" % workerdir)
                    if "zmq_auth_key" in worker:
                        shutil.copy(worker["zmq_auth_key"], "%s/zmq_auth/" % workerdir)
                        shutil.copy(worker["zmq_auth_key_secret"], "%s/zmq_auth/" % workerdir)
                        if "zmq_auth_key" in fm:
                            shutil.copy(worker["zmq_auth_key"], "output/%s/%s/zmq_auth/" % (fm["host"], fm["name"]))
                    else:
                        zmq_auth_genlist.write("%s/%s %s/%s\n" % (host, name, fm["host"], fm["name"]))
                        need_zmq_auth_gen = True
        if remote_token is "BAD":
            print("Cannot find %s on %s" % (remote_user, remote_master))
            sys.exit(1)
        if not "remote_proto" in worker:
            remote_proto = "http"
        else:
            remote_proto = worker["remote_proto"]
        remote_uri = "%s://%s:%s@%s:%s/RPC2" % (remote_proto, remote_user, remote_token, remote_address, remote_rpc_port)
        dockcomp["services"][worker_name]["environment"]["LAVA_MASTER_URI"] = remote_uri

        if "dispatcher_ip" in worker:
            dockcomp["services"][worker_name]["environment"]["LAVA_DISPATCHER_IP"] = worker["dispatcher_ip"]
        with open(dockcomposeymlpath, 'w') as f:
            yaml.dump(dockcomp, f)
        if "extra_actions" in worker:
            fp = open("%s/scripts/extra_actions" % workerdir, "w")
            for eaction in worker["extra_actions"]:
                fp.write(eaction)
                fp.write("\n")
            fp.close()
            os.chmod("%s/scripts/extra_actions" % workerdir, 0o755)

    if "boards" not in workers:
        print("Missing boards")
        sys.exit(1)
    ser2net_port = 60000
    boards = workers["boards"]
    for board in boards:
        board_name = board["name"]
        if "slave" in board:
            slave_name = board["slave"]
        else:
            slave_name = default_slave
        print("\tFound %s on %s" % (board_name, slave_name))
        found_slave = False
        for fs in workers["slaves"]:
            if fs["name"] == slave_name:
                slave = fs
                found_slave = True
        if not found_slave:
            print("Cannot find slave %s" % slave_name)
            sys.exit(1)
        if not "host" in slave:
            host = "local"
        else:
            host = slave["host"]
        workerdir = "output/%s/%s" % (host, slave_name)
        dockcomposeymlpath = "output/%s/docker-compose.yml" % host
        fp = open(dockcomposeymlpath, "r")
        dockcomp = yaml.load(fp)
        fp.close()
        device_path = "%s/devices/" % workerdir
        devices_path = "%s/devices/%s" % (workerdir, slave_name)
        devicetype = board["type"]
        device_line = template_device.substitute(devicetype=devicetype)
        if "pdu_generic" in board:
            hard_reset_command = board["pdu_generic"]["hard_reset_command"]
            power_off_command = board["pdu_generic"]["power_off_command"]
            power_on_command = board["pdu_generic"]["power_on_command"]
            device_line += template_device_pdu_generic.substitute(hard_reset_command=hard_reset_command, power_off_command=power_off_command, power_on_command=power_on_command)
        use_kvm = False
        if "kvm" in board:
            use_kvm = board["kvm"]
        if use_kvm:
            if "devices" in dockcomp["services"][worker_name]:
                dc_devices = dockcomp["services"][worker_name]["devices"]
            else:
                dockcomp["services"][worker_name]["devices"] = []
                dc_devices = dockcomp["services"][worker_name]["devices"]
            dc_devices.append("/dev/kvm:/dev/kvm")
            # board specific hacks
        if devicetype == "qemu" and not use_kvm:
            device_line += "{% set no_kvm = True %}\n"
        if "uart" in board:
            uart = board["uart"]
            baud = board["uart"].get("baud", baud_default)
            idvendor = board["uart"]["idvendor"]
            idproduct = board["uart"]["idproduct"]
            if type(idproduct) == str:
                print("Please put hexadecimal IDs for product %s (like 0x%s)" % (board_name, idproduct))
                sys.exit(1)
            if type(idvendor) == str:
                print("Please put hexadecimal IDs for vendor %s (like 0x%s)" % (board_name, idvendor))
                sys.exit(1)
            if "serial" in uart:
                serial = board["uart"]["serial"]
                udev_line = template_udev_serial.substitute(board=board_name, serial=serial, idvendor="%04x" % idvendor, idproduct="%04x" % idproduct)
            else:
                devpath = board["uart"]["devpath"]
                udev_line = template_udev_devpath.substitute(board=board_name, devpath=devpath, idvendor="%04x" % idvendor, idproduct="%04x" % idproduct)
            if not os.path.isdir("output/%s/udev" % host):
                os.mkdir("output/%s/udev" % host)
            fp = open("output/%s/udev/99-lavaworker-udev.rules" % host, "a")
            fp.write(udev_line)
            fp.close()
            if "devices" in dockcomp["services"][worker_name]:
                dc_devices = dockcomp["services"][worker_name]["devices"]
            else:
                dockcomp["services"][worker_name]["devices"] = []
                dc_devices = dockcomp["services"][worker_name]["devices"]
            dc_devices.append("/dev/%s:/dev/%s" % (board_name, board_name))
            use_conmux = True
            use_ser2net = False
            use_screen = False
            if "use_ser2net" in uart:
                use_conmux = False
                use_ser2net = True
            if "use_screen" in uart:
                use_conmux = False
                use_screen = True
            if use_conmux:
                conmuxline = template_conmux.substitute(board=board_name, baud=baud)
                device_line += template_device_conmux.substitute(board=board_name)
                fp = open("%s/conmux/%s.cf" % (workerdir, board_name), "w")
                fp.write(conmuxline)
                fp.close()
            if use_ser2net:
                ser2net_line = template_ser2net.substitute(port=ser2net_port,baud=baud,board=board_name)
                device_line += template_device_ser2net.substitute(port=ser2net_port)
                ser2net_port += 1
                fp = open("%s/ser2net.conf" % workerdir, "a")
                fp.write(ser2net_line)
                fp.close()
            if use_screen:
                device_line += template_device_screen.substitute(board=board_name)
                fp = open("%s/lava-screen.conf" % workerdir, "a")
                fp.write("%s\n" % board_name)
                fp.close()
        elif "connection_command" in board:
            connection_command = board["connection_command"]
            device_line += template_device_connection_command.substitute(connection_command=connection_command)
        if "uboot_ipaddr" in board:
            device_line += "{%% set uboot_ipaddr_cmd = 'setenv ipaddr %s' %%}\n" % board["uboot_ipaddr"]
        if "uboot_macaddr" in board:
            device_line += '{% set uboot_set_mac = true %}'
            device_line += "{%% set uboot_mac_addr = '%s' %%}\n" % board["uboot_macaddr"]
        if "fastboot_serial_number" in board:
            fserial = board["fastboot_serial_number"]
            device_line += "{%% set fastboot_serial_number = '%s' %%}" % fserial
        if "custom_option" in board:
            for coption in board["custom_option"]:
                device_line += "{%% %s %%}" % coption
        if not os.path.isdir(device_path):
            os.mkdir(device_path)
        if not os.path.isdir(devices_path):
            os.mkdir(devices_path)
        board_device_file = "%s/%s.jinja2" % (devices_path, board_name)
        fp = open(board_device_file, "w")
        fp.write(device_line)
        fp.close()
        with open(dockcomposeymlpath, 'w') as f:
            yaml.dump(dockcomp, f)
    zmq_auth_genlist.close()
    if need_zmq_auth_gen:
        print("Gen ZMQ auth files")
        subprocess.check_call(["./zmqauth/zmq_auth_fill.sh"], stdin=None)


if __name__ == "__main__":
    shutil.copy("common/build-lava", "lava-slave/scripts/build-lava")
    shutil.copy("common/build-lava", "lava-master/scripts/build-lava")
    main()

