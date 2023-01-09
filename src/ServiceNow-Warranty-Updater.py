import configparser
from datetime import datetime
import itertools
import logging
from logging.handlers import SysLogHandler
import os
import sys
import time
import unicodedata

import pysnow
from pysnow import exceptions
import pytz
from oauthlib.oauth2 import BackendApplicationClient
from requests_oauthlib import OAuth2Session


# Module information.
__author__ = 'Anthony Farina'
__copyright__ = 'Copyright (C) 2023 Anthony Farina'
__credits__ = ['Anthony Farina']
__maintainer__ = 'Anthony Farina'
__email__ = 'farinaanthony96@gmail.com'
__license__ = 'MIT'
__version__ = '1.0.0'
__status__ = 'Released'


# Configuration file access variables.
CONFIG = configparser.ConfigParser()
CONFIG_PATH = '/../configs/ServiceNow-Warranty-Updater-config.ini'
SCRIPT_PATH = os.path.dirname(os.path.realpath(__file__))
CONFIG.read(SCRIPT_PATH + CONFIG_PATH)

# ServiceNow API credentials.
SNOW_INSTANCE = CONFIG['ServiceNow Info']['instance']
SNOW_USERNAME = CONFIG['ServiceNow Info']['username']
SNOW_PASSWORD = CONFIG['ServiceNow Info']['password']
SNOW_CMDB_PATH = CONFIG['ServiceNow Info']['cmdb-table']
SNOW_CLIENT = pysnow.Client(instance=SNOW_INSTANCE,
                            user=SNOW_USERNAME,
                            password=SNOW_PASSWORD)

# Cisco Support API credentials.
CISCO_CLIENT_ID = CONFIG['Cisco Info']['client-id']
CISCO_CLIENT_SECRET = CONFIG['Cisco Info']['client-secret']
CISCO_TOKEN_URL = CONFIG['Cisco Info']['token-url']
CISCO_BASE_WARRANTY_URL = CONFIG['Cisco Info']['base-warranty-url']
CISCO_BASE_EOX_URL = CONFIG['Cisco Info']['base-eox-url']

# Dell TechDirect (Warranty) API credentials.
DELL_CLIENT_ID = CONFIG['Dell Info']['client-id']
DELL_CLIENT_SECRET = CONFIG['Dell Info']['client-secret']
DELL_TOKEN_URL = CONFIG['Dell Info']['token-url']
DELL_BASE_WARRANTY_URL = CONFIG['Dell Info']['base-warranty-url']

# Logger constant global variables.
LOGGER_NAME = CONFIG['Logger Info']['name']


# Get all Cisco records from ServiceNow and return it as a dictionary. The
# key is the Cisco device's serial number and the value is the record.
def get_snow_cisco_records() -> dict[str, dict[str, str]]:
    global_logger.info('=====================================================')
    global_logger.info('Getting all Cisco records from ServiceNow...')

    # Get all Cisco records from ServiceNow.
    snow_cmdb_table = SNOW_CLIENT.resource(api_path=SNOW_CMDB_PATH)
    snow_cisco_query = (pysnow.QueryBuilder().
                        field('name').order_ascending().
                        AND().
                        field('u_active_contract').equals('true').
                        AND().
                        field('manufacturer').contains('Cisco').
                        OR().
                        field('manufacturer').contains('Meraki')
                        )
    snow_cisco_resp = snow_cmdb_table.get(
        query=snow_cisco_query,
        fields=['sys_id', 'name', 'serial_number', 'asset_tag',
                'u_active_support_contract', 'warranty_expiration',
                'u_end_of_life', 'u_valid_warranty_data']
    )
    snow_cisco_devs = snow_cisco_resp.all()

    # Go through all Cisco records and extract valid records.
    snow_cisco_dict = dict()
    no_sn = 0
    collisions = 0
    for cisco_dev in snow_cisco_devs:
        # Check if there is no S/N or an invalid character(s) in the S/N field.
        cis_dev_sn = clean_serial_number(cisco_dev['serial_number'])
        if cis_dev_sn == '':
            # Check the 'asset_tag' field for a valid S/N.
            cis_dev_sn = clean_serial_number(cisco_dev['asset_tag'])
            if cis_dev_sn == '':
                # Invalid S/N found in the asset tag field too.
                update_snow_cisco_invalid_data(cisco_dev, 'Invalid S/N')
                no_sn += 1
                continue

            # Update the 'serial_number' field in ServiceNow from the
            # 'asset_tag' field.
            update_snow_cisco_sn(cisco_dev, cis_dev_sn)

        # Check if this record is a duplicate. Skip if so.
        if cis_dev_sn in snow_cisco_dict.keys():
            collisions += 1
            continue

        # Add this record to the Cisco devices dictionary.
        cisco_dev['serial_number'] = cis_dev_sn
        snow_cisco_dict[cis_dev_sn] = cisco_dev

    # Output information found while iterating through the Cisco records.
    global_logger.info(f'Valid Cisco records: {len(snow_cisco_dict.keys())}')
    global_logger.info(f'Invalid S/N found: {no_sn}')
    global_logger.info(f'Duplicates found: {collisions}')
    global_logger.info('All valid Cisco records retrieved from ServiceNow!')
    global_logger.info('=====================================================')

    return snow_cisco_dict


# Get all Dell records from ServiceNow and return it as a dictionary. The
# key is the Dell device's service tag and the value is the record.
def get_snow_dell_records() -> dict[str, dict[str, str]]:
    global_logger.info('=====================================================')
    global_logger.info('Getting all Dell records from ServiceNow...')

    # Get all Dell devices from ServiceNow.
    snow_cmdb_table = SNOW_CLIENT.resource(api_path=SNOW_CMDB_PATH)
    snow_dell_query = (pysnow.QueryBuilder().
                       field('name').order_ascending().
                       AND().
                       field('u_active_contract').equals('true').
                       AND().
                       field('manufacturer').contains('Dell')
                       )
    snow_dell_resp = snow_cmdb_table.get(
        query=snow_dell_query,
        fields=['sys_id', 'name', 'serial_number', 'asset_tag',
                'u_active_support_contract', 'warranty_expiration',
                'u_end_of_life', 'u_valid_warranty_data']
    )
    snow_dell_devs = snow_dell_resp.all()

    # Go through all Dell records and extract valid records.
    snow_dell_dict = dict()
    no_sn = 0
    collisions = 0
    for dell_dev in snow_dell_devs:
        # Check if this device has a valid service tag in the S/N field.
        dell_dev_service_tag = clean_service_tag(dell_dev['serial_number'])
        if dell_dev_service_tag == '':
            # Invalid service tag in the 'serial_number' field. Let's check
            # the 'asset_tag' field for a valid service tag.
            dell_dev_service_tag = clean_service_tag(dell_dev['asset_tag'])
            if dell_dev_service_tag == '':
                # Invalid service tag found in the asset_tag field too.
                update_snow_dell_invalid_data(dell_dev, 'Invalid S/N')
                no_sn += 1
                continue

            # Update the 'serial_number' field from the 'asset_tag' field in
            # ServiceNow.
            update_snow_dell_sn(dell_dev, dell_dev_service_tag)

        # Check if this record is a duplicate. Skip if so.
        if dell_dev_service_tag in snow_dell_dict.keys():
            collisions += 1
            continue

        # Add this record to the Dell devices dictionary.
        dell_dev['serial_number'] = dell_dev_service_tag
        snow_dell_dict[dell_dev_service_tag] = dell_dev

    # Output information found while iterating through the Dell records.
    global_logger.info(f'Valid Dell records: {len(snow_dell_dict.keys())}')
    global_logger.info(f'Invalid service tags found: {no_sn}')
    global_logger.info(f'Duplicates found: {collisions}')
    global_logger.info('All valid Dell records retrieved from ServiceNow!')
    global_logger.info('=====================================================')

    return snow_dell_dict


# Given a dictionary of Cisco devices, update their ServiceNow records with
# warranty and end-of-life information.
def update_snow_cisco_warranties(snow_cisco_devs: dict[str, dict[str, str]]):
    global_logger.info('=====================================================')
    global_logger.info('Updating valid Cisco records in ServiceNow...')

    # Get a Cisco Support API token to establish a connection to the API.
    warranty_client = BackendApplicationClient(client_id=CISCO_CLIENT_ID)
    warranty_oauth = OAuth2Session(client=warranty_client)
    warranty_token = warranty_oauth.fetch_token(
        token_url=CISCO_TOKEN_URL,
        client_id=CISCO_CLIENT_ID,
        client_secret=CISCO_CLIENT_SECRET)
    warranty_client = OAuth2Session(CISCO_CLIENT_ID, token=warranty_token)

    # Get a Cisco EOX API token to establish a connection to the API.
    eox_client = BackendApplicationClient(client_id=CISCO_CLIENT_ID)
    eox_oauth = OAuth2Session(client=eox_client)
    eox_token = eox_oauth.fetch_token(token_url=CISCO_TOKEN_URL,
                                      client_id=CISCO_CLIENT_ID,
                                      client_secret=CISCO_CLIENT_SECRET)
    eox_client = OAuth2Session(CISCO_CLIENT_ID, token=eox_token)

    # Get all provided Cisco device's warranty summaries / End-Of-life
    # information in batches of 20 (This is the maximum the Cisco EOX API
    # allows in 1 batch, which is less than the maximum of 50 for the Cisco
    # Support API in 1 batch)
    for batch in batcher(list(snow_cisco_devs.keys()), 20):
        # Prepare the batch request for Cisco warranties.
        sn_batch = ','.join(batch)
        warranty_url = CISCO_BASE_WARRANTY_URL + sn_batch

        # Get the warranty summary batch and convert it to JSON.
        warranty_resp = warranty_client.get(url=warranty_url)
        warranty_batch_resp = warranty_resp.json()

        # Iterate through this batch and update ServiceNow.
        for cis_dev in warranty_batch_resp['serial_numbers']:
            # Check if the API didn't find a device with this S/N.
            if 'ErrorResponse' in cis_dev.keys():
                # Check if the Cisco API gave back a weird S/N. Skip if so.
                if cis_dev['sr_no'] not in snow_cisco_devs.keys():
                    global_logger.error(
                        f'Cisco API error - weird S/N returned: '
                        f'{cis_dev["sr_no"]}')
                    continue

                # Update the 'u_valid_warranty_data' field in ServiceNow to
                # false.
                update_snow_cisco_invalid_data(
                    snow_cisco_devs[cis_dev['sr_no']], 'Cisco Support API '
                                                       'Error Response')
                continue

            # Update this record.
            update_snow_cisco_record(cis_dev,
                                     snow_cisco_devs[cis_dev['sr_no']])

        # Prepare the batch request for Cisco EOX.
        eox_url = CISCO_BASE_EOX_URL + sn_batch

        # Get the EOX batch and convert it to JSON.
        eox_resp = eox_client.get(url=eox_url,
                                  params={
                                      'responseencoding': 'json'
                                  })
        eox_batch_resp = eox_resp.json()

        # Check if this is a valid batch...
        if 'EOXRecord' not in eox_batch_resp.keys():
            global_logger.error('Invalid EOXRecord found')
            continue

        # Iterate through this batch and update ServiceNow.
        for cis_devs in eox_batch_resp['EOXRecord']:
            eol_str = cis_devs['LastDateOfSupport']['value']

            # There could be multiple records with the same EoL information,
            # so we need to loop through each one.
            for cis_dev_sn in cis_devs['EOXInputValue'].split(','):
                # Check if this device has no End-Of-Life information.
                if eol_str == '':
                    update_snow_cisco_no_eol(snow_cisco_devs[cis_dev_sn])
                    continue

                # Update this record.
                update_snow_cisco_eol(snow_cisco_devs[cis_dev_sn], eol_str)

    global_logger.info('Valid Cisco records updated in ServiceNow!')
    global_logger.info('=====================================================')


# Given a dictionary of Dell devices, update their ServiceNow records with
# warranty information.
def update_snow_dell_warranties(snow_dell_devs: dict[str, dict[str, str]]):
    global_logger.info('=====================================================')
    global_logger.info('Updating valid Dell records in ServiceNow...')

    # Get a Dell TechDirect API token to establish a connection to the API.
    client = BackendApplicationClient(client_id=DELL_CLIENT_ID)
    oauth = OAuth2Session(client=client)
    token = oauth.fetch_token(token_url=DELL_TOKEN_URL,
                              client_id=DELL_CLIENT_ID,
                              client_secret=DELL_CLIENT_SECRET)
    client = OAuth2Session(DELL_CLIENT_ID, token=token)

    # Get all provided Dell device's warranty summaries in batches of 100.
    # This is the maximum the Dell TechDirect API allows.
    for batch in batcher(list(snow_dell_devs.keys()), 100):
        # Prepare the batch request for Dell warranties.
        sn_batch = ','.join(batch)

        # Get the warranty batch and convert it to JSON.
        warranty_resp = client.get(url=DELL_BASE_WARRANTY_URL,
                                   headers={
                                       'Accept': 'application/json'
                                   },
                                   params={
                                       'servicetags': sn_batch
                                   })
        batch_resp = warranty_resp.json()

        # Iterate through this batch and update ServiceNow.
        for dell_dev in batch_resp:
            # Check if the API didn't find a device with this service tag.
            if dell_dev['id'] is None:
                # Weird exception...
                if dell_dev['serviceTag'] == 'AMALONE':
                    continue

                # Update the 'u_valid_warranty_data' field in ServiceNow to
                # false.
                update_snow_dell_invalid_data(
                    snow_dell_devs[dell_dev['serviceTag']],
                    'Dell Warranty API Error Response')
                continue

            # Update this record.
            update_snow_dell_record(dell_dev,
                                    snow_dell_devs[dell_dev['serviceTag']])

    global_logger.info('Valid Dell records updated in ServiceNow!')
    global_logger.info('=====================================================')


# Return specified batches of an iterable object.
# Credit: @georg from stackoverflow, with slight modifications
# Link: https://stackoverflow.com/a/28022548
def batcher(iterable, batch_size):
    # Make an iterator object from the iterable.
    iterator = iter(iterable)

    # Return each batch one at a time using yield.
    while True:
        batch = tuple(itertools.islice(iterator, batch_size))
        if not batch:
            break
        yield batch


# Attempts to remove corrupt characters from a given serial number. If
# successful, returns the cleaned serial number. Otherwise, returns the
# empty string: ''.
def clean_serial_number(serial_number: str):
    # Remove corrupted characters and spaces from the serial number.
    clean_sn = unicodedata.normalize('NFKD', serial_number).replace(' ', '')
    return '' if '/' in clean_sn or '\\' in clean_sn else clean_sn


# Attempts to remove corrupt characters from a given service tag and checks if
# the length is consistent with a valid service tag. If successful, returns
# the cleaned service tag. Otherwise, returns the empty string: ''.
def clean_service_tag(service_tag: str):
    # Remove corrupted characters and spaces from the service tag.
    clean_st = unicodedata.normalize('NFKD', service_tag).replace(' ', '')
    return '' if '/' in clean_st or '\\' in clean_st or len(clean_st) > 7 \
                 or len(clean_st) < 5 else clean_st


# Given a Cisco device and the related ServiceNow record, update ServiceNow
# if the records don't match.
def update_snow_cisco_record(cis_dev, snow_cis_dev):
    # Make variable to store any updates needed in ServiceNow.
    snow_update = {}

    # Check if this Cisco device has a warranty or is covered by a support
    # contract.
    if cis_dev['warranty_end_date'] == '' and cis_dev['is_covered'] != 'YES':
        if snow_cis_dev['u_valid_warranty_data'] != 'false':
            snow_cis_dev['u_valid_warranty_data'] = 'false'
            snow_update['u_valid_warranty_data'] = 'false'
    else:
        if snow_cis_dev['u_valid_warranty_data'] != 'true':
            snow_cis_dev['u_valid_warranty_data'] = 'true'
            snow_update['u_valid_warranty_data'] = 'true'

    # Check if the warranty end date is not in ServiceNow.
    if snow_cis_dev['warranty_expiration'] != cis_dev['warranty_end_date']:
        snow_cis_dev['warranty_expiration'] = cis_dev['warranty_end_date']
        snow_update['warranty_expiration'] = cis_dev['warranty_end_date']

    # Make sure SNow reflects that this warranty data is valid.
    if cis_dev['is_covered'] != 'YES':
        if snow_cis_dev['u_active_support_contract'] != 'false':
            snow_cis_dev['u_active_support_contract'] = 'false'
            snow_update['u_active_support_contract'] = 'false'
    else:
        if snow_cis_dev['u_active_support_contract'] != 'true':
            snow_cis_dev['u_active_support_contract'] = 'true'
            snow_update['u_active_support_contract'] = 'true'

    # Update ServiceNow if needed.
    if snow_update:
        snow_cmdb_table = SNOW_CLIENT.resource(api_path=SNOW_CMDB_PATH)
        global_logger.info(f'Updating Cisco record: {snow_cis_dev["name"]}')

        # Try to update this record.
        try:
            snow_cmdb_table.update(
                query={
                    'sys_id': snow_cis_dev['sys_id']
                },
                payload=snow_update
            )
        except exceptions.MultipleResults:
            # We got multiple results. Must be a duplicate.
            global_logger.error(
                f'Duplicate Cisco record can not be updated: '
                f'{snow_cis_dev["name"]}')
            return
        except exceptions.NoResults:
            # We didn't get any results. We can't update this record.
            global_logger.error(
                f'Cisco record could not be found: {snow_cis_dev["name"]}')
            return

        global_logger.info('Cisco record updated!')


# Given a Dell device and the related ServiceNow record, update ServiceNow
# if the records don't match.
def update_snow_dell_record(dell_dev, snow_dell_dev):
    # Make variable to store any updates needed in ServiceNow.
    snow_update = {}

    # Check if this Dell device has a warranty end date.
    if len(dell_dev['entitlements']) == 0:
        update_snow_dell_no_warranty(snow_dell_dev)
        return

    # Get the warranty end as a string.
    dell_warranty_end = \
        dell_dev['entitlements'][len(dell_dev['entitlements']) - 1][
            'endDate'][:10]

    # Check if the warranty end date is not in ServiceNow.
    if snow_dell_dev['warranty_expiration'] != dell_warranty_end:
        snow_dell_dev['warranty_expiration'] = dell_warranty_end
        snow_update['warranty_expiration'] = dell_warranty_end

    # Check this field.
    if snow_dell_dev['u_valid_warranty_data'] != 'true':
        snow_dell_dev['u_valid_warranty_data'] = 'true'
        snow_update['u_valid_warranty_data'] = 'true'

    # Update ServiceNow if needed.
    if snow_update:
        snow_cmdb_table = SNOW_CLIENT.resource(api_path=SNOW_CMDB_PATH)
        global_logger.info(f'Updating Dell record: {snow_dell_dev["name"]}')

        # Try to update this record.
        try:
            snow_cmdb_table.update(
                query={
                    'sys_id': snow_dell_dev['sys_id']
                },
                payload=snow_update
            )
        except exceptions.MultipleResults:
            # We got multiple results. Must be a duplicate.
            global_logger.error(
                f'Duplicate Dell record can not be updated:'
                f' {snow_dell_dev["name"]}')
            return
        except exceptions.NoResults:
            # We didn't get any results. We can't update this record.
            global_logger.error(
                f'Dell record could not be found: {snow_dell_dev["name"]}')
            return

        global_logger.info('Dell record updated!')


# Given a Dell device with no warranty and the related ServiceNow record,
# update ServiceNow if the records don't match.
def update_snow_dell_no_warranty(snow_dell_dev):
    global_logger.info(
        f'No warranty detected for Dell record: {snow_dell_dev["name"]}')
    snow_update = {}

    # Check if the warranty end date is not in ServiceNow.
    if snow_dell_dev['warranty_expiration'] != '':
        snow_dell_dev['warranty_expiration'] = ''
        snow_update['warranty_expiration'] = ''

    # Make sure SNow reflects that this warranty data is not valid.
    if snow_dell_dev['u_valid_warranty_data'] != 'false':
        snow_dell_dev['u_valid_warranty_data'] = 'false'
        snow_update['u_valid_warranty_data'] = 'false'

    # Update ServiceNow if needed.
    if snow_update:
        snow_cmdb_table = SNOW_CLIENT.resource(api_path=SNOW_CMDB_PATH)
        global_logger.info(f'Updating Dell record: {snow_dell_dev["name"]}')

        # Try to update this record.
        try:
            snow_cmdb_table.update(
                query={
                    'sys_id': snow_dell_dev['sys_id']
                },
                payload=snow_update
            )
        except exceptions.MultipleResults:
            # We got multiple results. Must be a duplicate.
            global_logger.error(
                f'Duplicate Dell record can not be updated: '
                f'{snow_dell_dev["name"]}')
            return
        except exceptions.NoResults:
            # We didn't get any results. We can't update this record.
            global_logger.error(
                f'Dell record could not be found: {snow_dell_dev["name"]}')
            return

        global_logger.info('Dell record updated!')


# Update the 'serial_number' field to a valid serial number in ServiceNow
# for a given Cisco device.
def update_snow_cisco_sn(snow_cis_dev, new_sn):
    snow_cmdb_table = SNOW_CLIENT.resource(api_path=SNOW_CMDB_PATH)
    global_logger.info(f'S/N found in the asset tag field! Updating S/N '
                       f'field for Cisco record: {snow_cis_dev["name"]}')

    # Try to update this record.
    try:
        snow_cmdb_table.update(
            query={
                'sys_id': snow_cis_dev['sys_id']
            },
            payload={
                'serial_number': new_sn
            }
        )
    except exceptions.MultipleResults:
        # We got multiple results. Must be a duplicate.
        global_logger.error(
            f'Duplicate Cisco record can not be updated: '
            f'{snow_cis_dev["name"]}')
        return
    except exceptions.NoResults:
        # We didn't get any results. We can't update this record.
        global_logger.error(
            f'Cisco record could not be found: {snow_cis_dev["name"]}')
        return

    global_logger.info(f'S/N field updated to {new_sn}!')


# Update the 'serial_number' field to a valid serial number in ServiceNow
# for a given Dell device.
def update_snow_dell_sn(snow_dell_dev, new_sn):
    snow_cmdb_table = SNOW_CLIENT.resource(api_path=SNOW_CMDB_PATH)
    global_logger.info(f'S/N found in the asset tag field! Updating S/N '
                       f'field for Dell record: {snow_dell_dev["name"]}')

    # Try to update this record.
    try:
        snow_cmdb_table.update(
            query={
                'sys_id': snow_dell_dev['sys_id']
            },
            payload={
                'serial_number': new_sn
            }
        )
    except exceptions.MultipleResults:
        # We got multiple results. Must be a duplicate.
        global_logger.error(f'Duplicate Dell record can not be updated:'
                            f' {snow_dell_dev["name"]}')
        return
    except exceptions.NoResults:
        # We didn't get any results. We can't update this record.
        global_logger.error(
            f'Dell record could not be found: {snow_dell_dev["name"]}')
        return

    global_logger.info(f'S/N field updated to {new_sn}!')


# Update the invalid warranty field for the given Cisco device in ServiceNow.
def update_snow_cisco_invalid_data(snow_cis_dev, invalid_reason):
    global_logger.warning(
        f'Invalid data for Cisco device {snow_cis_dev["name"]}. Reason: '
        f'{invalid_reason}')
    snow_cmdb_table = SNOW_CLIENT.resource(api_path=SNOW_CMDB_PATH)
    snow_update = {}

    # Check if this field is set correctly.
    if snow_cis_dev['u_valid_warranty_data'] != 'false':
        snow_cis_dev['u_valid_warranty_data'] = 'false'
        snow_update['u_valid_warranty_data'] = 'false'

    # Check if this ServiceNow record needs to be updated.
    if snow_update:
        # Try to update this record.
        try:
            snow_cmdb_table.update(
                query={
                    'sys_id': snow_cis_dev['sys_id']
                },
                payload=snow_update
            )
        except exceptions.MultipleResults:
            # We got multiple results. Must be a duplicate.
            global_logger.error(
                f'Duplicate Cisco record can not be updated: '
                f'{snow_cis_dev["name"]}')
            return
        except exceptions.NoResults:
            # We didn't get any results. We can't update this record.
            global_logger.error(
                f'Cisco record could not be found: {snow_cis_dev["name"]}')
            return

        global_logger.info(
            'Valid data field for Cisco record has been updated to false!')


# Update the invalid warranty field for the given Dell device in ServiceNow.
def update_snow_dell_invalid_data(snow_dell_dev, invalid_reason):
    global_logger.warning(
        f'Invalid data for Dell device {snow_dell_dev["name"]}. Reason:'
        f' {invalid_reason}')
    snow_cmdb_table = SNOW_CLIENT.resource(api_path=SNOW_CMDB_PATH)
    snow_update = {}

    # Check if this field is set correctly.
    if snow_dell_dev['u_valid_warranty_data'] != 'false':
        snow_dell_dev['u_valid_warranty_data'] = 'false'
        snow_update['u_valid_warranty_data'] = 'false'

    # Check if this ServiceNow record needs to be updated.
    if snow_update:
        # Try to update this record.
        try:
            snow_cmdb_table.update(
                query={
                    'sys_id': snow_dell_dev['sys_id']
                },
                payload=snow_update
            )
        except exceptions.MultipleResults:
            # We got multiple results. Must be a duplicate.
            global_logger.error(
                f'Duplicate Dell record can not be updated:'
                f' {snow_dell_dev["name"]}')
            return
        except exceptions.NoResults:
            # We didn't get any results. We can't update this record.
            global_logger.error(
                f'Dell record could not be found: {snow_dell_dev["name"]}')
            return

        global_logger.info(
            'Valid data field for Dell record has been updated to false!')


# This function will update the provided record into ServiceNow with
# the provided end-of-life string.
def update_snow_cisco_eol(snow_cis_dev, eol_str):
    snow_cmdb_table = SNOW_CLIENT.resource(api_path=SNOW_CMDB_PATH)
    snow_update = {}

    # Check if this field is set correctly.
    if snow_cis_dev['u_end_of_life'] != eol_str:
        snow_cis_dev['u_end_of_life'] = eol_str
        snow_update['u_end_of_life'] = eol_str

    if snow_cis_dev['u_valid_warranty_data'] == 'false':
        snow_cis_dev['u_valid_warranty_data'] = 'true'
        snow_update['u_valid_warranty_data'] = 'true'

    # Check if this ServiceNow record needs to be updated.
    if snow_update:
        global_logger.info(
            f'Updating EoL for Cisco device: {snow_cis_dev["name"]}')

        # Try to update this record.
        try:
            snow_cmdb_table.update(
                query={
                    'sys_id': snow_cis_dev['sys_id']
                },
                payload=snow_update
            )
        except exceptions.MultipleResults:
            # We got multiple results. Must be a duplicate.
            global_logger.error(
                f'Duplicate Cisco record can not be updated: '
                f'{snow_cis_dev["name"]}')
            return
        except exceptions.NoResults:
            # We didn't get any results. We can't update this record.
            global_logger.error(
                f'Cisco record could not be found: {snow_cis_dev["name"]}')
            return

        global_logger.info('EoL was updated!')


# This function will update the provided record into ServiceNow with no
# end-of-life information.
def update_snow_cisco_no_eol(snow_cis_dev):
    global_logger.warning(
        f'No EOL information found for Cisco device {snow_cis_dev["name"]}')
    snow_cmdb_table = SNOW_CLIENT.resource(api_path=SNOW_CMDB_PATH)
    snow_update = {}

    # Check if this field is set correctly.
    if snow_cis_dev['u_end_of_life'] != '':
        snow_cis_dev['u_end_of_life'] = ''
        snow_update['u_end_of_life'] = ''

    # Check if this ServiceNow record needs to be updated.
    if snow_update:
        global_logger.info(
            f'Updating EoL for Cisco device: {snow_cis_dev["name"]}')

        # Try to update this record.
        try:
            snow_cmdb_table.update(
                query={
                    'sys_id': snow_cis_dev['sys_id']
                },
                payload=snow_update
            )
        except exceptions.MultipleResults:
            # We got multiple results. Must be a duplicate.
            global_logger.error(
                f'Duplicate Cisco record can not be updated: '
                f'{snow_cis_dev["name"]}')
            return
        except exceptions.NoResults:
            # We didn't get any results. We can't update this record.
            global_logger.error(
                f'Cisco record could not be found: {snow_cis_dev["name"]}')
            return

        global_logger.info('EoL was updated!')


# Returns the global logger for this script. Logs will be generated for the
# console, a log file, and Paper Trail.
def make_logger() -> logging.Logger:
    # Make the logger's timestamps in UTC.
    logging.Formatter.converter = time.gmtime

    # Initialize a format for the log file and standard out handlers.
    stdout_file_format = logging.Formatter(
        '%(asctime)s [%(name)s] [%(levelname)s] %(message)s',
        datefmt='%b %d %Y %H:%M:%S UTC')

    # Initialize and configure the standard out handler for logging to the
    # console.
    stdout_handle = logging.StreamHandler(sys.stdout)
    stdout_handle.setLevel(logging.INFO)
    stdout_handle.setFormatter(stdout_file_format)

    # Initialize and configure the log file handler for logging to a file.
    now_utc = datetime.utcnow().replace(tzinfo=pytz.UTC)

    # Check if the "logs" folder exists. If not, create it.
    if not os.path.isdir(SCRIPT_PATH + '/../logs'):
        os.mkdir(SCRIPT_PATH + '/../logs')

    log_file_handle = logging.FileHandler(
        SCRIPT_PATH + '/../logs/warranty_updater_log_' +
        now_utc.strftime('%Y-%m-%d_%H-%M-%S-%Z') + '.log')
    log_file_handle.setLevel(logging.INFO)
    log_file_handle.setFormatter(stdout_file_format)

    # Initialize and configure the remote system handler for logging to
    # Paper Trail.
    paper_trail_handle = SysLogHandler(address=('logs.papertrailapp.com',
                                                49638))
    paper_trail_handle.setLevel(logging.INFO)
    paper_trail_handle.setFormatter(
        logging.Formatter(LOGGER_NAME + ': %(message)s'))

    # Initialize the global logger and add the standard out, file, and remote
    # handlers to it.
    logger = logging.getLogger(name=LOGGER_NAME)
    logger.addHandler(stdout_handle)
    logger.addHandler(log_file_handle)
    logger.addHandler(paper_trail_handle)
    logger.setLevel(logging.INFO)

    return logger


# Main method to run the script.
if __name__ == '__main__':
    # Make the global logger for this script.
    global_logger = make_logger()

    # Get Cisco devices.
    snow_cisco_records_dict = get_snow_cisco_records()

    # Update Cisco devices in ServiceNow.
    update_snow_cisco_warranties(snow_cisco_records_dict)

    # Get Dell devices.
    snow_dell_records_dict = get_snow_dell_records()

    # Update Dell devices in ServiceNow.
    update_snow_dell_warranties(snow_dell_records_dict)
