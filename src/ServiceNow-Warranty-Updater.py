from datetime import datetime
import itertools
import logging
from logging.handlers import SysLogHandler
import os
import re
import sys
import time

import dotenv
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
__version__ = '2.0.9'
__status__ = 'Released'


# Set up the extraction of global constants from the environment variable file.
dotenv.load_dotenv('./../.env')

# ServiceNow API constant global variables.
SNOW_INSTANCE = os.getenv('SNOW_INSTANCE')
SNOW_USERNAME = os.getenv('SNOW_USERNAME')
SNOW_PASSWORD = os.getenv('SNOW_PASSWORD')
SNOW_CI_TABLE_PATH = os.getenv('SNOW_CI_TABLE_PATH')
SNOW_CLIENT = pysnow.Client(instance=SNOW_INSTANCE,
                            user=SNOW_USERNAME,
                            password=SNOW_PASSWORD)

# Cisco Support and End-of-Life API constant global variables.
CISCO_CLIENT_KEY = os.getenv('CISCO_CLIENT_KEY')
CISCO_CLIENT_SECRET = os.getenv('CISCO_CLIENT_SECRET')
CISCO_AUTH_TOKEN_URI = os.getenv('CISCO_AUTH_TOKEN_URI')
CISCO_WARRANTY_URI = os.getenv('CISCO_WARRANTY_URI')
CISCO_EOX_URI = os.getenv('CISCO_EOX_URI')

# Dell TechDirect (Warranty) API constant global variables.
DELL_CLIENT_KEY = os.getenv('DELL_CLIENT_KEY')
DELL_CLIENT_SECRET = os.getenv('DELL_CLIENT_SECRET')
DELL_AUTH_TOKEN_URI = os.getenv('DELL_AUTH_TOKEN_URI')
DELL_WARRANTY_URI = os.getenv('DELL_WARRANTY_URI')

# Logger constant global variables.
LOGGER_NAME = os.getenv('LOGGER_NAME')
LOGGER = None
PAPERTRAIL_ADDRESS = os.getenv('PAPERTRAIL_ADDRESS')
PAPERTRAIL_PORT = os.getenv('PAPERTRAIL_PORT')

# Other constant global variables.
CISCO_SEARCH_TERMS = ['Cisco', 'Meraki']
DELL_SEARCH_TERMS = ['Dell']
INVALID_SN_CHARS_REGEX = r'[^-a-z0-9A-Z]'
SCRIPT_PATH = os.path.dirname(os.path.realpath(__file__))
SNOW_REQUIRED_FIELDS = ['sys_id', 'name', 'manufacturer', 'serial_number',
                        'u_active_support_contract', 'warranty_expiration',
                        'u_end_of_life', 'u_valid_warranty_data']


class SNowRecord:
    """
    Represents a record inside a ServiceNow instance.

    :param snow_sys_id: The ServiceNow system identifier of the device.
    :param name: The name of the device.
    :param manufacturer: The manufacturer of the device.
    :param serial_number: The serial number of the device.
    :param active_support_contract: Custom ServiceNow boolean string that
        denotes a device is under an active contract (for warranties?).
    :param warranty_expiration: The date that the warranty for this device
        expires.
    :param end_of_life: The date that marks the end of life for this device.
    :param valid_warranty_data: The boolean string that states if this device
        has valid warranty data.
    :param update_snow: Boolean that states if the script should update this
        device to ServiceNow because new information was found about this
        device that needs to be updated.
    """

    # Class fields.
    snow_sys_id: str
    name: str
    manufacturer: str
    serial_number: str
    active_support_contract: str
    warranty_expiration: str
    end_of_life: str
    valid_warranty_data: str
    update_snow: bool

    # Class initializer.
    def __init__(self, snow_sys_id, name, manufacturer, serial_number,
                 active_support_contract, warranty_expiration, end_of_life,
                 valid_warranty_data, update_snow=False):
        self.snow_sys_id = snow_sys_id
        self.name = name
        self.manufacturer = manufacturer
        self.serial_number = serial_number
        self.active_support_contract = active_support_contract
        self.warranty_expiration = warranty_expiration
        self.end_of_life = end_of_life
        self.valid_warranty_data = valid_warranty_data
        self.update_snow = update_snow


def get_records_from_snow(manufacturer_search_terms: list[str]) -> \
        list[dict[str, str]]:
    """
    Gets all active records from ServiceNow that contain the search term(s)
    inside their "Manufacturer" field.

    :param manufacturer_search_terms: List of strings that the manufacturer
    field should contain.

    :return: An iterable list of records from ServiceNow.
    """

    LOGGER.info(f'Retrieving {"/".join(manufacturer_search_terms)} records '
                f'from ServiceNow...')

    # Create the connection to the configuration item (CI) table.
    snow_ci_table = SNOW_CLIENT.resource(api_path=SNOW_CI_TABLE_PATH)

    # Create the query for the CI table.
    snow_ci_query = (pysnow.QueryBuilder().
                     field('name').order_ascending().
                     AND().
                     field('u_active_contract').equals('true')
                     )

    # Add the search terms to the query.
    is_first_term = True
    for search_term in manufacturer_search_terms:
        # We need to add an "AND" operator before the first search term.
        if is_first_term:
            snow_ci_query = (snow_ci_query.
                             AND().
                             field('manufacturer').contains(search_term)
                             )
            is_first_term = False
            continue

        # Add another search term.
        snow_ci_query = (snow_ci_query.
                         OR().
                         field('manufacturer').contains(search_term)
                         )

    # Send the query to ServiceNow.
    snow_resp = snow_ci_table.get(
        query=snow_ci_query,
        fields=SNOW_REQUIRED_FIELDS
    )

    LOGGER.info('Records retrieved!')

    # Return the records.
    return snow_resp.all()


def extract_valid_records(snow_records: list[dict[str, str]]) -> \
        dict[str, SNowRecord]:
    """
    Given a list of ServiceNow records, extract and return only the valid
    records. Valid records will have an appropriate, non-empty string in the
    serial number field.

    :param snow_records: A list of ServiceNow records.

    :return: A dictionary where keys are serial numbers and the values are
        the fields associated with that device.
    """

    LOGGER.info(f'Validating ServiceNow records...')

    # Setup values needed for the return object.
    valid_records = dict()
    update_snow = False

    # Go through all given records from ServiceNow.
    for record in snow_records:
        # Make this record's serial number easier to reference.
        curr_sn = record['serial_number']

        # Check if the serial number field is blank.
        if curr_sn is None or curr_sn == '':
            # No serial number found.
            LOGGER.warning('No serial number found for ServiceNow '
                           f'{record["manufacturer"]} record: {record["name"]}')
            continue

        # Check if the serial number field was filled in with nonsense.
        if curr_sn == 'N/A' or curr_sn == 'TBD':
            # Yell at engineers for not filling in the serial number field
            # correctly when onboarding a customer's devices.
            LOGGER.warning('A silly serial number was found for ServiceNow '
                           f'{record["manufacturer"]} record: {record["name"]}'
                           f' | {curr_sn}')
            continue

        # Clean the current serial number.
        clean_sn = clean_serial_number(curr_sn)

        # Check if the serial number has been seen before.
        if clean_sn in valid_records.keys():
            # Duplicate serial number found.
            LOGGER.warning('Duplicate serial number found for ServiceNow '
                           f'{record["manufacturer"]} record: {clean_sn}')
            continue

        # Check if the serial number was cleaned.
        if clean_sn != record['serial_number']:
            update_snow = True

        # Add this ServiceNow record to the valid records' dictionary.
        valid_records[clean_sn] = \
            SNowRecord(
                snow_sys_id=record['sys_id'],
                name=record['name'],
                manufacturer=record['manufacturer'],
                serial_number=clean_sn,
                active_support_contract=record['u_active_support_contract'],
                warranty_expiration=record['warranty_expiration'],
                end_of_life=record['u_end_of_life'],
                valid_warranty_data=record['u_valid_warranty_data'],
                update_snow=update_snow
            )

    LOGGER.info('ServiceNow records validated!')

    # Return all valid records from ServiceNow.
    return valid_records


def clean_serial_number(serial_number: str) -> str:
    """Removes corrupt and invalid characters from a given serial number and
    returns the cleaned serial number.

    :param serial_number: The serial number string to remove corrupt and invalid
        characters from.

    :return: The cleaned serial number string.
    """

    # Remove corrupted and invalid characters from the serial number.
    return re.sub(INVALID_SN_CHARS_REGEX, '', serial_number)


def update_cisco_devices_with_warranties(
        cisco_records: dict[str, SNowRecord]) -> None:
    """
    Updates the provided Cisco records with updated warranty information via
    the Cisco Support API.

    :param cisco_records: The valid Cisco records to update with warranty
        information.
    """

    LOGGER.info('Retrieving Cisco warranty information...')

    # Get a Cisco Support API token to establish a connection to the API.
    cisco_warranty_client = BackendApplicationClient(client_id=CISCO_CLIENT_KEY)
    cisco_warranty_oauth = OAuth2Session(client=cisco_warranty_client)
    cisco_warranty_token = cisco_warranty_oauth.fetch_token(
        token_url=CISCO_AUTH_TOKEN_URI,
        client_id=CISCO_CLIENT_KEY,
        client_secret=CISCO_CLIENT_SECRET)
    cisco_warranty_client = OAuth2Session(CISCO_CLIENT_KEY,
                                          token=cisco_warranty_token)

    # Get all provided Cisco device's warranty summaries in batches of 75
    # (the maximum batch size for this API endpoint).
    for batch in batcher(list(cisco_records.keys()), 75):
        # Prepare the batch request for Cisco warranties.
        sn_batch = ','.join(batch)
        cisco_warranty_url = CISCO_WARRANTY_URI + sn_batch

        # Get the warranty summary batch.
        cisco_warranty_resp = cisco_warranty_client.get(url=cisco_warranty_url)

        # Check if the request was not successful.
        if cisco_warranty_resp.status_code != 200:
            LOGGER.error(f'Status code {cisco_warranty_resp.status_code} '
                         f'received from the Cisco Warranty API. Reason: '
                         f'{cisco_warranty_resp.reason}')
            continue

        # The request was successful, so let's convert it to JSON.
        cisco_warranty_batch_resp = cisco_warranty_resp.json()

        # Iterate through this batch and update the Cisco devices.
        for cisco_device in cisco_warranty_batch_resp['serial_numbers']:
            # Check if the API returned an error for this serial number.
            if 'ErrorResponse' in cisco_device.keys():
                # Extract and print the error returned from the Cisco API.
                error_response = \
                    (cisco_device["ErrorResponse"]["APIError"][
                        "ErrorDescription"])
                LOGGER.error(f'The Cisco Warranty API ran into an error for '
                             f'Cisco record with serial number '
                             f'{cisco_device["sr_no"]}. Reason: '
                             f'{error_response}')

                # Check if there is a corrupt character in this serial number.
                # if 'A parameter is incorrectly formatted' in error_response:
                #     print('Weird S/N found. Record will not be updated.')

                continue

            # The response was valid, so let's extract the Cisco device from
            # the provided valid Cisco records.
            cisco_record = cisco_records.get(cisco_device['sr_no'])

            # Check if we cannot back-reference the serial number to the
            # provided valid Cisco records.
            if not cisco_record:
                LOGGER.error(f'Unable to reference Cisco record back to '
                             f'ServiceNow with serial number '
                             f'{cisco_device["sr_no"]}')
                continue

            # Update this Cisco device with updated warranty information.
            update_cisco_device_warranty(cisco_record, cisco_device)


def update_cisco_device_warranty(cisco_device: SNowRecord,
                                 warranty_info: dict) -> None:
    """
    Updates the provided Cisco device using the provided warranty information.

    :param cisco_device: The Cisco device to update.
    :param warranty_info: The warranty information to update the Cisco device
        with.
    """

    # Check if this Cisco device lacks a warranty or is not covered by a support
    # contract.
    if warranty_info['warranty_end_date'] == '' and \
            warranty_info['is_covered'] != 'YES':
        if cisco_device.valid_warranty_data != 'false':
            cisco_device.valid_warranty_data = 'false'
            cisco_device.update_snow = True
    else:
        if cisco_device.valid_warranty_data != 'true':
            cisco_device.valid_warranty_data = 'true'
            cisco_device.update_snow = True

    # Check if the warranty end date is not in ServiceNow.
    if cisco_device.warranty_expiration != warranty_info['warranty_end_date']:
        cisco_device.warranty_expiration = warranty_info['warranty_end_date']
        cisco_device.update_snow = True

    # Make sure SNow reflects that this warranty data is valid.
    if warranty_info['is_covered'] != 'YES':
        if cisco_device.active_support_contract != 'false':
            cisco_device.active_support_contract = 'false'
            cisco_device.update_snow = True
    else:
        if cisco_device.active_support_contract != 'true':
            cisco_device.active_support_contract = 'true'
            cisco_device.update_snow = True


def update_cisco_devices_with_eols(cisco_records: dict[str, SNowRecord]) -> \
        None:
    """
    Updates the provided Cisco records with updated end-of-life information via
    the Cisco Support API.

    :param cisco_records: The valid Cisco records to update with end-of-life
        information.
    """

    LOGGER.info('Retrieving Cisco end-of-life information...')

    # Get a Cisco EOX API token to establish a connection to the API.
    cisco_eox_client = BackendApplicationClient(client_id=CISCO_CLIENT_KEY)
    cisco_eox_oauth = OAuth2Session(client=cisco_eox_client)
    cisco_eox_token = cisco_eox_oauth.fetch_token(
        token_url=CISCO_AUTH_TOKEN_URI,
        client_id=CISCO_CLIENT_KEY,
        client_secret=CISCO_CLIENT_SECRET)
    cisco_eox_client = OAuth2Session(CISCO_CLIENT_KEY, token=cisco_eox_token)

    # Get all provided Cisco device's end of life summaries in batches of 20
    # (the maximum batch size for this API endpoint).
    for batch in batcher(list(cisco_records.keys()), 20):
        # Prepare the batch request for Cisco EOX.
        sn_batch = ','.join(batch)
        cisco_eox_url = CISCO_EOX_URI + sn_batch

        # Get the EOX batch and convert it to JSON.
        cisco_eox_resp = cisco_eox_client.get(
            url=cisco_eox_url, params={'responseencoding': 'json'}
        )

        # Check if the request was not successful.
        if cisco_eox_resp.status_code != 200:
            LOGGER.error(f'Status code {cisco_eox_resp.status_code} '
                         f'received from the Cisco EOX API. Reason: '
                         f'{cisco_eox_resp.reason}')
            continue

        # The request was successful, so let's convert it to JSON.
        cisco_eox_batch_resp = cisco_eox_resp.json()

        # Check if this is a valid batch.
        if 'EOXRecord' not in cisco_eox_batch_resp.keys():
            LOGGER.error('The Cisco EOX API ran into an error for a batch of '
                         'Cisco records likely due to an erroneous serial '
                         'number.')
            LOGGER.error(cisco_eox_batch_resp)
            continue

        # Iterate through this batch and update the Cisco device.
        for cisco_device in cisco_eox_batch_resp['EOXRecord']:
            end_of_life_str = cisco_device['LastDateOfSupport']['value']

            # There could be multiple records with the same EoL information,
            # so we need to loop through each one.
            for cisco_device_sn in cisco_device['EOXInputValue'].split(','):
                # Get the related Cisco device with this serial number.
                cisco_record = cisco_records.get(cisco_device_sn)

                # Check if we could not reference this device back to
                # ServiceNow.
                if not cisco_record:
                    LOGGER.error(f'Unable to reference Cisco record back to '
                                 f'ServiceNow with serial number '
                                 f'{cisco_device["sr_no"]}')
                    continue

                # Update this Cisco device with updated end-of-life information.
                update_cisco_device_eol(cisco_record, end_of_life_str)


def update_cisco_device_eol(cisco_record: SNowRecord,
                            end_of_life_date_string: str) -> None:
    """
    Updates the provided Cisco device using the provided end of life
    information.

    :param cisco_record: The Cisco device to update.
    :param end_of_life_date_string: The end of life information to update the
        Cisco device with.
    """

    # Check if this device's EoL needs to be updated.
    if cisco_record.end_of_life != end_of_life_date_string:
        cisco_record.end_of_life = end_of_life_date_string
        cisco_record.update_snow = True


def sync_records_back_to_snow(snow_records: dict[str, SNowRecord]) -> None:
    """
    Updates the provided ServiceNow records back into the CMDB. Will only
    update a record if a field was updated from an API with new information.

    :param snow_records: The ServiceNow records to update.
    """

    LOGGER.info('Synchronizing records back to ServiceNow...')

    # Go through each record and sync it back to ServiceNow, if appropriate.
    for snow_record in snow_records.values():
        # Check if ServiceNow should be updated.
        if snow_record.update_snow:
            snow_ci_table = SNOW_CLIENT.resource(api_path=SNOW_CI_TABLE_PATH)
            LOGGER.info(f'Syncing {snow_record.manufacturer} record to '
                        f'ServiceNow: {snow_record.name}')

            # Try to update this record.
            try:
                snow_ci_table.update(
                    query={
                        'sys_id': snow_record.snow_sys_id
                    },
                    payload={
                        'warranty_expiration': snow_record.warranty_expiration,
                        'u_end_of_life': snow_record.end_of_life,
                        'serial_number': snow_record.serial_number,
                        'u_active_support_contract':
                            snow_record.active_support_contract,
                        'u_valid_warranty_data':
                            snow_record.valid_warranty_data
                    }
                )
            except exceptions.MultipleResults:
                # We got multiple results. Must be a duplicate.
                LOGGER.error(f'Duplicate {snow_record.manufacturer} record '
                             f'found: {snow_record.name}')
                continue
            except exceptions.NoResults:
                # We didn't get any results. We can't update this record.
                LOGGER.error(f'{snow_record.manufacturer} record could not '
                             f'be found: {snow_record.name}')
                continue


def update_dell_devices_with_warranties(dell_records: dict[str, SNowRecord]) \
        -> None:
    """
    Updates the provided Dell records with updated warranty information via
    the Dell TechDirect API.

    :param dell_records: The valid Dell records to update with warranty
        information.
    """

    LOGGER.info('Retrieving Dell warranty information...')

    # Get a Dell TechDirect API token to establish a connection to the API.
    dell_warranty_client = BackendApplicationClient(client_id=DELL_CLIENT_KEY)
    dell_warranty_oauth = OAuth2Session(client=dell_warranty_client)
    dell_warranty_token = dell_warranty_oauth.fetch_token(
        token_url=DELL_AUTH_TOKEN_URI,
        client_id=DELL_CLIENT_KEY,
        client_secret=DELL_CLIENT_SECRET)
    dell_warranty_client = OAuth2Session(DELL_CLIENT_KEY,
                                         token=dell_warranty_token)

    # Get all provided Dell device's warranty summaries in batches of 100.
    # This is the maximum the Dell TechDirect API allows.
    for batch in batcher(list(dell_records.keys()), 100):
        # Prepare the batch request for Dell warranties.
        sn_batch = ','.join(batch)

        # Get the warranty batch and convert it to JSON.
        dell_warranty_resp = dell_warranty_client.get(
            url=DELL_WARRANTY_URI,
            headers={'Accept': 'application/json'},
            params={'servicetags': sn_batch}
        )

        # Check if the request was not successful.
        if dell_warranty_resp.status_code != 200:
            LOGGER.error(f'Status code {dell_warranty_resp.status_code} '
                         f'received from the Dell TechDirect API. Reason: '
                         f'{dell_warranty_resp.reason}')
            continue

        # The request was successful, so let's convert it to JSON.
        dell_warranty_batch_resp = dell_warranty_resp.json()

        # Iterate through this batch of Dell devices.
        for dell_device in dell_warranty_batch_resp:
            # Check for an errored Dell device here. Otherwise, do the get.
            dell_record = dell_records.get(dell_device['serviceTag'])

            # Check if we cannot back-reference the serial number provided to
            # ServiceNow.
            if not dell_record:
                LOGGER.error(f'Unable to reference Dell record back to '
                             f'ServiceNow with serial number '
                             f'{dell_device["serviceTag"]}')
                continue

            # Update this Dell device with updated warranty information.
            update_dell_device_warranty(dell_record, dell_device)


def update_dell_device_warranty(dell_device: SNowRecord, warranty_info: dict) \
        -> None:
    """
    Updates the provided Dell device using the provided warranty information.

    :param dell_device: The Dell device to update.
    :param warranty_info: The warranty information to update the Dell device
        with.
    """

    # Check if the warranty info is invalid or there is no warranty information.
    if warranty_info['invalid'] or len(warranty_info['entitlements']) == 0:
        # Check if the Dell device matches the state of the warranty
        # information.
        if dell_device.valid_warranty_data != 'false':
            dell_device.valid_warranty_data = 'false'
            dell_device.update_snow = True

        # Check if the Dell device reflects that it is not under an active
        # support contract.
        if dell_device.active_support_contract != 'false':
            dell_device.active_support_contract = 'false'
            dell_device.update_snow = True

        return

    # Check if the Dell device matches the state of the warranty information.
    if dell_device.valid_warranty_data != 'true':
        dell_device.valid_warranty_data = 'true'
        dell_device.update_snow = True

    # Get the warranty end date as a string.
    dell_warranty_end_date = \
        warranty_info['entitlements'][len(warranty_info['entitlements']) - 1][
            'endDate'][:10]

    # Check if the warranty end date is not in ServiceNow.
    if dell_device.warranty_expiration != dell_warranty_end_date:
        dell_device.warranty_expiration = dell_warranty_end_date
        dell_device.update_snow = True


def batcher(iterable, batch_size: int):
    """
    Splits the provided iterable object into configurable batches.

    Credit: @georg from stackoverflow, with slight modifications.

    Link: https://stackoverflow.com/a/28022548

    :param iterable: An iterable object to split into batches.
    :type iterable: Any iterable object.
    :param batch_size: The size of each batch to be returned.

    :return: Iterable objects of size "batch_size".
    :rtype: Any iterable object.
    """

    # Make an iterator object from the iterable.
    iterator = iter(iterable)

    # Return each batch one at a time using yield.
    while True:
        batch = tuple(itertools.islice(iterator, batch_size))

        # Check if we can no longer make batches.
        if not batch:
            # End the loop.
            break

        # Return this batch.
        yield batch


def make_logger() -> logging.Logger:
    """
    Returns the global logger for this script. Logs will be generated for the
    console, a log file, and Paper Trail.

    :return: The script's global Logger object.
    """

    # Make the logger's timestamps in UTC.
    logging.Formatter.converter = time.gmtime

    # Initialize a format for the log file and standard-out handlers.
    stdout_file_format = logging.Formatter(
        '%(asctime)s [%(name)s] [%(levelname)s] %(message)s',
        datefmt='%b %d %Y %H:%M:%S UTC')

    # Initialize and configure the standard-out handler for logging to the
    # console.
    stdout_handle = logging.StreamHandler(sys.stdout)
    stdout_handle.setLevel(logging.INFO)
    stdout_handle.setFormatter(stdout_file_format)

    # Initialize and configure the log file handler for logging to a file.
    now_utc = datetime.utcnow().replace(tzinfo=pytz.UTC)

    # Check if the "logs" folder exists. If not, create it.
    if not os.path.isdir(SCRIPT_PATH + '/../logs'):
        os.mkdir(SCRIPT_PATH + '/../logs')

    # Initialize and configure the log file handler for logging to a file.
    log_file_handle = logging.FileHandler(
        SCRIPT_PATH + '/../logs/warranty_updater_log_' +
        now_utc.strftime('%Y-%m-%d_%H-%M-%S-%Z') + '.log')
    log_file_handle.setLevel(logging.INFO)
    log_file_handle.setFormatter(stdout_file_format)

    # Initialize and configure the remote system handler for logging to
    # Paper Trail.
    paper_trail_handle = SysLogHandler(address=(PAPERTRAIL_ADDRESS,
                                                int(PAPERTRAIL_PORT)))
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

    # Return the logger object.
    return logger


def main() -> None:
    """
    Main method that runs the script.
    """

    # Get all active Cisco records from ServiceNow.
    active_snow_cisco_records = get_records_from_snow(CISCO_SEARCH_TERMS)

    # Filter out blank and corrupt serial numbers from the Cisco records.
    valid_snow_cisco_records = extract_valid_records(active_snow_cisco_records)

    # Use the Cisco Support API to extract warranty dates and update the
    # Cisco device objects in memory.
    update_cisco_devices_with_warranties(valid_snow_cisco_records)

    # Use the Cisco EOX API to extract end of life dates and update the Cisco
    # device objects in memory.
    update_cisco_devices_with_eols(valid_snow_cisco_records)

    # Synchronize the Cisco devices in memory to ServiceNow, based on if we
    # were able to extract updated information from the Cisco APIs.
    sync_records_back_to_snow(valid_snow_cisco_records)

    # Get all active Dell records from ServiceNow.
    active_snow_dell_records = get_records_from_snow(DELL_SEARCH_TERMS)

    # Filter out blank and corrupt serial numbers from the Dell records.
    valid_snow_dell_records = extract_valid_records(active_snow_dell_records)

    # Use the Dell TechDirect API to extract warranty dates and update the Dell
    # device objects in memory.
    update_dell_devices_with_warranties(valid_snow_dell_records)

    # Synchronize the Dell devices in memory to ServiceNow, based on if we
    # were able to extract updated information from the Dell TechDirect API.
    sync_records_back_to_snow(valid_snow_dell_records)


if __name__ == '__main__':
    # Make the global logger for this script.
    LOGGER = make_logger()

    # Run the script.
    main()
