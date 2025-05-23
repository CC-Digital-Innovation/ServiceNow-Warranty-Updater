from dataclasses import dataclass
import itertools
import os
import re
import urllib3

from dotenv import load_dotenv
from loguru import logger
from oauthlib.oauth2 import BackendApplicationClient
import pysnow
from pysnow import exceptions
from requests_oauthlib import OAuth2Session


# ====================== Environment / Global Variables =======================
load_dotenv(override=True)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# ServiceNow API constant global variables.
SERVICENOW_INSTANCE = os.getenv('SERVICENOW_INSTANCE')
SERVICENOW_USERNAME = os.getenv('SERVICENOW_USERNAME')
SERVICENOW_PASSWORD = os.getenv('SERVICENOW_PASSWORD')
SERVICENOW_CI_TABLE_PATH = os.getenv('SERVICENOW_CI_TABLE_PATH')
SERVICENOW_CLIENT = pysnow.Client(
    instance=SERVICENOW_INSTANCE,
    user=SERVICENOW_USERNAME,
    password=SERVICENOW_PASSWORD
)

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

# Other constant global variables.
CISCO_SEARCH_TERMS = ['Cisco', 'Meraki']
DELL_SEARCH_TERMS = ['Dell']
INVALID_SN_CHARS_REGEX = r'[^-a-z0-9A-Z]'
SNOW_REQUIRED_FIELDS = [
    'sys_id', 'name', 'manufacturer', 'manufacturer.name',
    'serial_number', 'u_active_support_contract',
    'warranty_expiration', 'u_end_of_life', 'u_valid_warranty_data',
    'company'
]


# ================================== Classes ==================================
@dataclass
class SNowRecord:
    """
    Represents a record inside a ServiceNow instance.

    :param snow_sys_id (str): The ServiceNow system identifier of the record.
    :param name (str): The name of the device the record is referring to.
    :param manufacturer (str): The manufacturer of the device.
    :param serial_number (str): The serial number of the device.
    :param active_support_contract (str): Custom ServiceNow boolean string that
        denotes a device is under an active contract (for warranties?).
    :param warranty_expiration (str): The date that the warranty for this device
        expires.
    :param end_of_life (str): The date that marks the end of life for this device.
    :param valid_warranty_data (str): The boolean string that states if this device
        has valid warranty data.
    :param update_snow (bool): Boolean that states if the script should update this
        record in ServiceNow because new information was found about this
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
    update_snow: bool = False


# ================================= Functions =================================
def get_records_from_snow(manufacturer_search_terms: list[str]) -> \
        list[dict[str, str]]:
    """
    Gets all active records from ServiceNow that contain the search term(s)
    inside their "Manufacturer" field.

    :param manufacturer_search_terms (list[str]): List of strings that the manufacturer
        field should contain.

    :return (list[dict[str, str]]): An iterable list of records from ServiceNow.
    """

    logger.info(f'Retrieving {"/".join(manufacturer_search_terms)} records '
                f'from ServiceNow...')

    # Create the connection to the configuration item (CI) table.
    snow_ci_table = SERVICENOW_CLIENT.resource(api_path=SERVICENOW_CI_TABLE_PATH)

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

    logger.info(f'{"/".join(manufacturer_search_terms)} records retrieved!')

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
        the fields associated with that record.
    """

    logger.info(f'Validating ServiceNow records...')

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
            logger.warning('No serial number found for ServiceNow '
                           f'{record["manufacturer.name"]} record:'
                           f' {record["name"]}')
            continue

        # Check if the serial number field was filled in with nonsense.
        if curr_sn == 'N/A' or curr_sn == 'TBD':
            # Yell at engineers for not filling in the serial number field
            # correctly when onboarding a customer's devices into records.
            logger.warning('A silly serial number was found for ServiceNow '
                           f'{record["manufacturer.name"]} record:'
                           f' {record["name"]} | {curr_sn}')
            continue

        # Clean the current serial number.
        clean_sn = clean_serial_number(curr_sn)

        # Check if the serial number has been seen before.
        if clean_sn in valid_records.keys():
            # Duplicate serial number found.
            logger.warning('Duplicate serial number found for ServiceNow '
                           f'{record["manufacturer.name"]} record: {clean_sn}')
            continue

        # Check if the serial number was cleaned.
        if clean_sn != record['serial_number']:
            update_snow = True

        # Add this ServiceNow record to the valid records' dictionary.
        valid_records[clean_sn] = \
            SNowRecord(
                snow_sys_id=record['sys_id'],
                name=record['name'],
                manufacturer=record['manufacturer.name'],
                serial_number=clean_sn,
                active_support_contract=record['u_active_support_contract'],
                warranty_expiration=record['warranty_expiration'],
                end_of_life=record['u_end_of_life'],
                valid_warranty_data=record['u_valid_warranty_data'],
                update_snow=update_snow
            )

    logger.info('ServiceNow records validated!')

    # Return all valid records from ServiceNow.
    return valid_records


def clean_serial_number(serial_number: str) -> str:
    """
    Removes corrupt and invalid characters from a given serial number and
    returns the cleaned serial number.

    :param serial_number: The serial number string to remove corrupt and invalid
        characters from.

    :return: The cleaned serial number string.
    """

    # Remove corrupted and invalid characters from the serial number.
    return re.sub(INVALID_SN_CHARS_REGEX, '', serial_number)


def update_cisco_records_with_warranties(
        cisco_records: dict[str, SNowRecord]) -> None:
    """
    Updates the provided Cisco records with updated warranty information via
    the Cisco Support API.

    :param cisco_records: The valid Cisco records to update with warranty
        information.
    """

    logger.info('Retrieving and updating Cisco records with warranty '
                'information...')

    # Get a Cisco Support API token to establish a connection to the API.
    cisco_warranty_client = BackendApplicationClient(client_id=CISCO_CLIENT_KEY)
    cisco_warranty_oauth = OAuth2Session(client=cisco_warranty_client)
    cisco_warranty_token = cisco_warranty_oauth.fetch_token(
        token_url=CISCO_AUTH_TOKEN_URI,
        client_id=CISCO_CLIENT_KEY,
        client_secret=CISCO_CLIENT_SECRET)
    cisco_warranty_client = OAuth2Session(CISCO_CLIENT_KEY,
                                          token=cisco_warranty_token)

    # Get all provided Cisco record's warranty summaries in batches of 75
    # (the maximum batch size for this API endpoint).
    for batch in batcher(list(cisco_records.keys()), 75):
        # Prepare the batch request for Cisco warranties.
        sn_batch = ','.join(batch)
        cisco_warranty_url = CISCO_WARRANTY_URI + sn_batch

        # Get the warranty summary batch.
        cisco_warranty_resp = cisco_warranty_client.get(
            url=cisco_warranty_url, verify=False)

        # Check if the request was not successful.
        if cisco_warranty_resp.status_code != 200:
            logger.error(f'Status code {cisco_warranty_resp.status_code} '
                         f'received from the Cisco Warranty API. Reason: '
                         f'{cisco_warranty_resp.reason}')
            continue

        # The request was successful, so let's convert it to JSON.
        cisco_warranty_batch_resp = cisco_warranty_resp.json()

        # Iterate through this batch and update the Cisco records.
        for cisco_device in cisco_warranty_batch_resp['serial_numbers']:
            # Check if the API returned an error for this serial number.
            if 'ErrorResponse' in cisco_device.keys():
                # Extract and print the error returned from the Cisco API.
                error_response = \
                    (cisco_device["ErrorResponse"]["APIError"][
                        "ErrorDescription"])
                logger.error(f'The Cisco Warranty API ran into an error for '
                             f'Cisco record with serial number '
                             f'{cisco_device["sr_no"]}. Reason: '
                             f'{error_response}')
                continue

            # The response was valid, so let's extract the Cisco record from
            # the provided valid Cisco records.
            cisco_record = cisco_records.get(cisco_device['sr_no'])

            # Check if we cannot back-reference the serial number to the
            # provided valid Cisco records.
            if not cisco_record:
                logger.error(f'Unable to reference Cisco record back to '
                             f'ServiceNow with serial number '
                             f'{cisco_device["sr_no"]}')
                continue

            # Update this Cisco record with updated warranty information.
            update_cisco_record_warranty(cisco_record, cisco_device)

    logger.info('Cisco records updated!')


def update_cisco_record_warranty(cisco_record: SNowRecord,
                                 warranty_info: dict) -> None:
    """
    Updates the provided Cisco record using the provided warranty information.

    :param cisco_record: The Cisco record to update.
    :param warranty_info: The warranty information to update the Cisco record
        with.
    """

    # Check if this Cisco record lacks a warranty or is not covered by a support
    # contract.
    if warranty_info['warranty_end_date'] == '' and \
            warranty_info['is_covered'] != 'YES':
        if cisco_record.valid_warranty_data != 'false':
            cisco_record.valid_warranty_data = 'false'
            cisco_record.update_snow = True
    else:
        if cisco_record.valid_warranty_data != 'true':
            cisco_record.valid_warranty_data = 'true'
            cisco_record.update_snow = True

    # Check if the warranty end date is not in ServiceNow.
    if cisco_record.warranty_expiration != warranty_info['warranty_end_date']:
        cisco_record.warranty_expiration = warranty_info['warranty_end_date']
        cisco_record.update_snow = True

    # Make sure SNow reflects that this warranty data is valid.
    if warranty_info['is_covered'] != 'YES':
        if cisco_record.active_support_contract != 'false':
            cisco_record.active_support_contract = 'false'
            cisco_record.update_snow = True
    else:
        if cisco_record.active_support_contract != 'true':
            cisco_record.active_support_contract = 'true'
            cisco_record.update_snow = True


def update_cisco_records_with_eols(cisco_records: dict[str, SNowRecord]) -> \
        None:
    """
    Updates the provided Cisco records with updated end-of-life information via
    the Cisco Support API.

    :param cisco_records: The valid Cisco records to update with end-of-life
        information.
    """

    logger.info('Retrieving and updating Cisco records with end-of-life '
                'information...')

    # Get a Cisco EOX API token to establish a connection to the API.
    cisco_eox_client = BackendApplicationClient(client_id=CISCO_CLIENT_KEY)
    cisco_eox_oauth = OAuth2Session(client=cisco_eox_client)
    cisco_eox_token = cisco_eox_oauth.fetch_token(
        token_url=CISCO_AUTH_TOKEN_URI,
        client_id=CISCO_CLIENT_KEY,
        client_secret=CISCO_CLIENT_SECRET)
    cisco_eox_client = OAuth2Session(CISCO_CLIENT_KEY, token=cisco_eox_token)

    # Get all provided Cisco record's end of life summaries in batches of 20
    # (the maximum batch size for this API endpoint).
    for batch in batcher(list(cisco_records.keys()), 20):
        # Prepare the batch request for Cisco EOX.
        sn_batch = ','.join(batch)
        cisco_eox_url = CISCO_EOX_URI + sn_batch

        # Get the EOX batch and convert it to JSON.
        cisco_eox_resp = cisco_eox_client.get(
            url=cisco_eox_url, params={'responseencoding': 'json'}, verify=False
        )

        # Check if the request was not successful.
        if cisco_eox_resp.status_code != 200:
            logger.error(f'Status code {cisco_eox_resp.status_code} '
                         f'received from the Cisco EOX API. Reason: '
                         f'{cisco_eox_resp.reason}')
            continue

        # The request was successful, so let's convert it to JSON.
        cisco_eox_batch_resp = cisco_eox_resp.json()

        # Check if this is a valid batch.
        if 'EOXRecord' not in cisco_eox_batch_resp.keys():
            logger.error('The Cisco EOX API ran into an error for a batch of '
                         'Cisco records likely due to an erroneous serial '
                         'number.')
            logger.error(cisco_eox_batch_resp)
            continue

        # Iterate through this batch and update the Cisco record.
        for cisco_device in cisco_eox_batch_resp['EOXRecord']:
            end_of_life_str = cisco_device['LastDateOfSupport']['value']

            # There could be multiple records with the same EoL information,
            # so we need to loop through each one.
            for cisco_device_sn in cisco_device['EOXInputValue'].split(','):
                # Get the related Cisco record with this serial number.
                cisco_record = cisco_records.get(cisco_device_sn)

                # Check if we could not reference this record back to
                # ServiceNow.
                if not cisco_record:
                    logger.error(f'Unable to reference Cisco record back to '
                                 f'ServiceNow with serial number '
                                 f'{cisco_device_sn}')
                    continue

                # Update this Cisco record with updated end-of-life information.
                update_cisco_record_eol(cisco_record, end_of_life_str)

    logger.info('Cisco records updated!')


def update_cisco_record_eol(cisco_record: SNowRecord,
                            end_of_life_date_string: str) -> None:
    """
    Updates the provided Cisco record using the provided end of life
    information.

    :param cisco_record: The Cisco record to update.
    :param end_of_life_date_string: The end of life information to update the
        Cisco record with.
    """

    # Check if this Cisco record's EoL needs to be updated.
    if cisco_record.end_of_life != end_of_life_date_string:
        cisco_record.end_of_life = end_of_life_date_string
        cisco_record.update_snow = True


def sync_records_back_to_snow(snow_records: dict[str, SNowRecord]) -> None:
    """
    Updates the provided ServiceNow records back into the CMDB. Will only
    update a record if a field was updated from an API with new information.

    :param snow_records: The ServiceNow records to update.
    """

    logger.info('Synchronizing records back to ServiceNow...')

    # Go through each record and sync it back to ServiceNow, if appropriate.
    for snow_record in snow_records.values():
        # Check if ServiceNow should be updated.
        if snow_record.update_snow:
            snow_ci_table = SERVICENOW_CLIENT.resource(api_path=SERVICENOW_CI_TABLE_PATH)
            logger.info(f'Syncing {snow_record.manufacturer} record to '
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
                logger.error(f'Duplicate {snow_record.manufacturer} record '
                             f'found: {snow_record.name}')
                continue
            except exceptions.NoResults:
                # We didn't get any results. We can't update this record.
                logger.error(f'{snow_record.manufacturer} record could not '
                             f'be found: {snow_record.name}')
                continue

    logger.info('Records synchronized with ServiceNow!')


def update_dell_records_with_warranties(dell_records: dict[str, SNowRecord]) \
        -> None:
    """
    Updates the provided Dell records with updated warranty information via
    the Dell TechDirect API.

    :param dell_records: The valid Dell records to update with warranty
        information.
    """

    logger.info('Retrieving and updating Dell records with warranty '
                'information...')

    # Get a Dell TechDirect API token to establish a connection to the API.
    dell_warranty_client = BackendApplicationClient(client_id=DELL_CLIENT_KEY)
    dell_warranty_oauth = OAuth2Session(client=dell_warranty_client)
    dell_warranty_token = dell_warranty_oauth.fetch_token(
        token_url=DELL_AUTH_TOKEN_URI,
        client_id=DELL_CLIENT_KEY,
        client_secret=DELL_CLIENT_SECRET)
    dell_warranty_client = OAuth2Session(DELL_CLIENT_KEY,
                                         token=dell_warranty_token)

    # Get all provided Dell record's warranty summaries in batches of 100.
    # This is the maximum the Dell TechDirect API allows.
    for batch in batcher(list(dell_records.keys()), 100):
        # Prepare the batch request for Dell warranties.
        sn_batch = ','.join(batch)

        # Get the warranty batch and convert it to JSON.
        dell_warranty_resp = dell_warranty_client.get(
            url=DELL_WARRANTY_URI,
            headers={'Accept': 'application/json'},
            params={'servicetags': sn_batch},
            verify=False
        )

        # Check if the request was not successful.
        if dell_warranty_resp.status_code != 200:
            logger.error(f'Status code {dell_warranty_resp.status_code} '
                         f'received from the Dell TechDirect API. Reason: '
                         f'{dell_warranty_resp.reason}')
            continue

        # The request was successful, so let's convert it to JSON.
        dell_warranty_batch_resp = dell_warranty_resp.json()

        # Iterate through this batch of Dell devices.
        for dell_device in dell_warranty_batch_resp:
            # Get the related Dell record with this serial number.
            dell_record = dell_records.get(dell_device['serviceTag'])

            # Check if we cannot back-reference the serial number provided to
            # ServiceNow.
            if not dell_record:
                logger.error(f'Unable to reference Dell record back to '
                             f'ServiceNow with serial number '
                             f'{dell_device["serviceTag"]}')
                continue

            # Update this Dell record with updated warranty information.
            update_dell_record_warranty(dell_record, dell_device)


def update_dell_record_warranty(dell_record: SNowRecord, warranty_info: dict) \
        -> None:
    """
    Updates the provided Dell record using the provided warranty information.

    :param dell_record: The Dell record to update.
    :param warranty_info: The warranty information to update the Dell record
        with.
    """

    # Check if the warranty info is invalid or there is no warranty information.
    if warranty_info['invalid'] or len(warranty_info['entitlements']) == 0:
        # Check if the Dell record matches the state of the warranty
        # information.
        if dell_record.valid_warranty_data != 'false':
            dell_record.valid_warranty_data = 'false'
            dell_record.update_snow = True

        # Check if the Dell record reflects that it is not under an active
        # support contract.
        if dell_record.active_support_contract != 'false':
            dell_record.active_support_contract = 'false'
            dell_record.update_snow = True

        return

    # Check if the Dell record matches the state of the warranty information.
    if dell_record.valid_warranty_data != 'true':
        dell_record.valid_warranty_data = 'true'
        dell_record.update_snow = True

    # Get the warranty end date as a string.
    dell_warranty_end_date = \
        warranty_info['entitlements'][len(warranty_info['entitlements']) - 1][
            'endDate'][:10]

    # Check if the warranty end date is not in ServiceNow.
    if dell_record.warranty_expiration != dell_warranty_end_date:
        dell_record.warranty_expiration = dell_warranty_end_date
        dell_record.update_snow = True


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


def run() -> None:
    """
    Method that runs the script.
    """

    # Get all active Cisco records from ServiceNow.
    active_snow_cisco_records = get_records_from_snow(CISCO_SEARCH_TERMS)

    # Filter out blank and corrupt serial numbers from the Cisco records.
    valid_snow_cisco_records = extract_valid_records(active_snow_cisco_records)

    # Use the Cisco Support API to extract warranty dates and update the
    # Cisco record objects in memory.
    update_cisco_records_with_warranties(valid_snow_cisco_records)

    # Use the Cisco EOX API to extract end of life dates and update the Cisco
    # record objects in memory.
    update_cisco_records_with_eols(valid_snow_cisco_records)

    # Synchronize the Cisco records in memory to ServiceNow, based on if we
    # were able to extract updated information from the Cisco APIs.
    sync_records_back_to_snow(valid_snow_cisco_records)

    # Get all active Dell records from ServiceNow.
    active_snow_dell_records = get_records_from_snow(DELL_SEARCH_TERMS)

    # Filter out blank and corrupt serial numbers from the Dell records.
    valid_snow_dell_records = extract_valid_records(active_snow_dell_records)

    # Use the Dell TechDirect API to extract warranty dates and update the Dell
    # record objects in memory.
    update_dell_records_with_warranties(valid_snow_dell_records)

    # Synchronize the Dell records in memory to ServiceNow, based on if we
    # were able to extract updated information from the Dell TechDirect API.
    sync_records_back_to_snow(valid_snow_dell_records)


if __name__ == '__main__':
    run()
