#!/usr/bin/env python3
#
# Copyright (C) 2020-2023 VyOS maintainers and contributors
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 or later as
# published by the Free Software Foundation.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.

import os

from itertools import product
from sys import exit

from netifaces import interfaces
from vyos.config import Config
from vyos.configdict import dict_merge
from vyos.configdict import node_changed
from vyos.configdict import leaf_node_changed
from vyos.template import render
from vyos.utils.process import call
from vyos import ConfigError
from vyos import airbag
airbag.enable()


def get_config(config=None):

    if config:
        conf = config
    else:
        conf = Config()
    base = ['policy']

    pbr = conf.get_config_dict(base, key_mangling=('-', '_'), get_first_key=True)

    for route in ['local_route', 'local_route6']:
        dict_id = 'rule_remove' if route == 'local_route' else 'rule6_remove'
        route_key = 'local-route' if route == 'local_route' else 'local-route6'
        base_rule = base + [route_key, 'rule']

        # delete policy local-route
        dict = {}
        tmp = node_changed(conf, base_rule, key_mangling=('-', '_'))
        if tmp:
            for rule in (tmp or []):
                src = leaf_node_changed(conf, base_rule + [rule, 'source', 'address'])
                fwmk = leaf_node_changed(conf, base_rule + [rule, 'fwmark'])
                iif = leaf_node_changed(conf, base_rule + [rule, 'inbound-interface'])
                dst = leaf_node_changed(conf, base_rule + [rule, 'destination', 'address'])
                proto = leaf_node_changed(conf, base_rule + [rule, 'protocol'])
                rule_def = {}
                if src:
                    rule_def = dict_merge({'source': {'address': src}}, rule_def)
                if fwmk:
                    rule_def = dict_merge({'fwmark' : fwmk}, rule_def)
                if iif:
                    rule_def = dict_merge({'inbound_interface' : iif}, rule_def)
                if dst:
                    rule_def = dict_merge({'destination': {'address': dst}}, rule_def)
                if proto:
                    rule_def = dict_merge({'protocol' : proto}, rule_def)
                dict = dict_merge({dict_id : {rule : rule_def}}, dict)
                pbr.update(dict)

        if not route in pbr:
            continue

        # delete policy local-route rule x source x.x.x.x
        # delete policy local-route rule x fwmark x
        # delete policy local-route rule x destination x.x.x.x
        if 'rule' in pbr[route]:
            for rule, rule_config in pbr[route]['rule'].items():
                src = leaf_node_changed(conf, base_rule + [rule, 'source', 'address'])
                fwmk = leaf_node_changed(conf, base_rule + [rule, 'fwmark'])
                iif = leaf_node_changed(conf, base_rule + [rule, 'inbound-interface'])
                dst = leaf_node_changed(conf, base_rule + [rule, 'destination', 'address'])
                proto = leaf_node_changed(conf, base_rule + [rule, 'protocol'])
                # keep track of changes in configuration
                # otherwise we might remove an existing node although nothing else has changed
                changed = False

                rule_def = {}
                # src is None if there are no changes to src
                if src is None:
                    # if src hasn't changed, include it in the removal selector
                    # if a new selector is added, we have to remove all previous rules without this selector
                    # to make sure we remove all previous rules with this source(s), it will be included
                    if 'source' in rule_config:
                        if 'address' in rule_config['source']:
                            rule_def = dict_merge({'source': {'address': rule_config['source']['address']}}, rule_def)
                else:
                    # if src is not None, it's previous content will be returned
                    # this can be an empty array if it's just being set, or the previous value
                    # either way, something has to be changed and we only want to remove previous values
                    changed = True
                    # set the old value for removal if it's not empty
                    if len(src) > 0:
                        rule_def = dict_merge({'source': {'address': src}}, rule_def)

                if fwmk is None:
                    if 'fwmark' in rule_config:
                        rule_def = dict_merge({'fwmark': rule_config['fwmark']}, rule_def)
                else:
                    changed = True
                    if len(fwmk) > 0:
                        rule_def = dict_merge({'fwmark' : fwmk}, rule_def)

                if iif is None:
                    if 'inbound_interface' in rule_config:
                        rule_def = dict_merge({'inbound_interface': rule_config['inbound_interface']}, rule_def)
                else:
                    changed = True
                    if len(iif) > 0:
                        rule_def = dict_merge({'inbound_interface' : iif}, rule_def)

                if dst is None:
                    if 'destination' in rule_config:
                        if 'address' in rule_config['destination']:
                            rule_def = dict_merge({'destination': {'address': rule_config['destination']['address']}}, rule_def)
                else:
                    changed = True
                    if len(dst) > 0:
                        rule_def = dict_merge({'destination': {'address': dst}}, rule_def)

                if proto is None:
                    if 'protocol' in rule_config:
                        rule_def = dict_merge({'protocol': rule_config['protocol']}, rule_def)
                else:
                    changed = True
                    if len(proto) > 0:
                        rule_def = dict_merge({'protocol' : proto}, rule_def)

                if changed:
                    dict = dict_merge({dict_id : {rule : rule_def}}, dict)
                    pbr.update(dict)

    return pbr

def verify(pbr):
    # bail out early - looks like removal from running config
    if not pbr:
        return None

    for route in ['local_route', 'local_route6']:
        if not route in pbr:
            continue

        pbr_route = pbr[route]
        if 'rule' in pbr_route:
            for rule in pbr_route['rule']:
                if (
                    'source' not in pbr_route['rule'][rule] and
                    'destination' not in pbr_route['rule'][rule] and
                    'fwmark' not in pbr_route['rule'][rule] and
                    'inbound_interface' not in pbr_route['rule'][rule] and
                    'protocol' not in pbr_route['rule'][rule]
                ):
                    raise ConfigError('Source or destination address or fwmark or inbound-interface or protocol is required!')

                if 'set' not in pbr_route['rule'][rule] or 'table' not in pbr_route['rule'][rule]['set']:
                    raise ConfigError('Table set is required!')

                if 'inbound_interface' in pbr_route['rule'][rule]:
                    interface = pbr_route['rule'][rule]['inbound_interface']
                    if interface not in interfaces():
                        raise ConfigError(f'Interface "{interface}" does not exist')

    return None

def generate(pbr):
    if not pbr:
        return None

    return None

def apply(pbr):
    if not pbr:
        return None

    # Delete old rule if needed
    for rule_rm in ['rule_remove', 'rule6_remove']:
        if rule_rm in pbr:
            v6 = " -6" if rule_rm == 'rule6_remove' else ""

            for rule, rule_config in pbr[rule_rm].items():
                source = rule_config.get('source', {}).get('address', [''])
                destination = rule_config.get('destination', {}).get('address', [''])
                fwmark = rule_config.get('fwmark', [''])
                inbound_interface = rule_config.get('inbound_interface', [''])
                protocol = rule_config.get('protocol', [''])

                for src, dst, fwmk, iif, proto in product(source, destination, fwmark, inbound_interface, protocol):
                    f_src = '' if src == '' else f' from {src} '
                    f_dst = '' if dst == '' else f' to {dst} '
                    f_fwmk = '' if fwmk == '' else f' fwmark {fwmk} '
                    f_iif = '' if iif == '' else f' iif {iif} '
                    f_proto = '' if proto == '' else f' ipproto {proto} '

                    call(f'ip{v6} rule del prio {rule} {f_src}{f_dst}{f_fwmk}{f_iif}')

    # Generate new config
    for route in ['local_route', 'local_route6']:
        if not route in pbr:
            continue

        v6 = " -6" if route == 'local_route6' else ""
        pbr_route = pbr[route]

        if 'rule' in pbr_route:
            for rule, rule_config in pbr_route['rule'].items():
                table = rule_config['set'].get('table', '')
                source = rule_config.get('source', {}).get('address', ['all'])
                destination = rule_config.get('destination', {}).get('address', ['all'])
                fwmark = rule_config.get('fwmark', '')
                inbound_interface = rule_config.get('inbound_interface', '')
                protocol = rule_config.get('protocol', '')

                for src in source:
                    f_src = f' from {src} ' if src else ''
                    for dst in destination:
                        f_dst = f' to {dst} ' if dst else ''
                        f_fwmk = f' fwmark {fwmark} ' if fwmark else ''
                        f_iif = f' iif {inbound_interface} ' if inbound_interface else ''
                        f_proto = f' ipproto {protocol} ' if protocol else ''

                        call(f'ip{v6} rule add prio {rule}{f_src}{f_dst}{f_proto}{f_fwmk}{f_iif} lookup {table}')

    return None

if __name__ == '__main__':
    try:
        c = get_config()
        verify(c)
        generate(c)
        apply(c)
    except ConfigError as e:
        print(e)
        exit(1)
