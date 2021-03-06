# Copyright 2017 Intel Corporation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.
# ------------------------------------------------------------------------------
import datetime
import getpass
import hashlib
import os

from sawtooth_cli.exceptions import CliException
from sawtooth_cli.rest_client import RestClient

from sawtooth_cli.protobuf.config_pb2 import ConfigPayload
from sawtooth_cli.protobuf.config_pb2 import ConfigProposal
from sawtooth_cli.protobuf.transaction_pb2 import TransactionHeader
from sawtooth_cli.protobuf.transaction_pb2 import Transaction
from sawtooth_cli.protobuf.batch_pb2 import BatchHeader
from sawtooth_cli.protobuf.batch_pb2 import Batch
from sawtooth_cli.protobuf.batch_pb2 import BatchList

from sawtooth_signing import secp256k1_signer as signing


def add_config_parser(subparsers, parent_parser):
    """Creates the arg parsers needed for the config command and
    its subcommands.
    """
    parser = subparsers.add_parser('config')

    parser.add_argument(
        '--url',
        type=str,
        help="the URL of the validator's REST API",
        default='http://localhost:8080')

    config_parsers = parser.add_subparsers(title="subcommands",
                                           dest="subcommand")
    config_parsers.required = True

    set_parser = config_parsers.add_parser(
        'set',
        help='creates batches of sawtooth-config transactions')

    set_parser.add_argument(
        '-k', '--key',
        type=str,
        help='the signing key for the resulting batches')
    set_parser.add_argument(
        '-o', '--output',
        type=str,
        default='config.batch',
        help='the name of the file to ouput the resulting batches')
    set_parser.add_argument(
        'setting',
        type=str,
        nargs='+',
        help='configuration setting, as a key/value pair: <key>=<value>')

    propose_parser = config_parsers.add_parser(
        'propose',
        help='creates and submits a Propose configuration transaction')

    propose_parser.add_argument(
        '-k', '--key',
        type=str,
        help='the signing key for the resulting batch')

    propose_parser.add_argument(
        'setting',
        type=str,
        help='the configuration setting key')

    propose_parser.add_argument(
        'value',
        type=str,
        help='the proposed value of the setting key')


def do_config(args):
    """Executes the config commands subcommands.
    """
    if args.subcommand == 'set':
        _do_config_set(args)
    elif args.subcommand == 'propose':
        _do_config_propose(args)
    else:
        raise AssertionError(
            '"{}" is not a valid subcommand of "config"'.format(
                args.subcommand))


def _do_config_set(args):
    """Executes the 'set' subcommand.  Given a key file, and a series of
    key/value pairs, it generates batches of sawtooth_config transactions in a
    BatchList instance, and stores it in a file.
    """
    settings = [s.split('=', 1) for s in args.setting]

    pubkey, signing_key = _read_signing_keys(args.key)

    txns = [_create_propose_txn(pubkey, signing_key, setting)
            for setting in settings]

    batch = _create_batch(pubkey, signing_key, txns)

    batch_list = BatchList(batches=[batch]).SerializeToString()

    try:
        with open(args.output, 'wb') as batch_file:
            batch_file.write(batch_list)
    except IOError as e:
        raise CliException(
            'Unable to write to batch file: {}'.format(str(e)))


def _do_config_propose(args):
    """Executes the 'propose' subcommand. Given a key file, a URL and a setting
    key and value, it generates a ConfigPayload.PROPOSE sawtooth_config
    transaction and submits it via the validator REST API.
    """
    pubkey, signing_key = _read_signing_keys(args.key)
    txn = _create_propose_txn(pubkey, signing_key, (args.setting, args.value))
    batch = _create_batch(pubkey, signing_key, [txn])
    batch_list = BatchList(batches=[batch])

    rest_client = RestClient(args.url)
    rest_client.send_batches(batch_list)


def _read_signing_keys(key_filename):
    """Reads the given file as a WIF formatted key.

    Args:
        key_filename: The filename where the key is stored. If None,
            defaults to the default key for the current user.

    Returns:
        tuple (str, str): the public and private key pair

    Raises:
        CliException: If unable to read the file.
    """
    filename = key_filename
    if filename is None:
        filename = os.path.join(os.path.expanduser('~'),
                                '.sawtooth',
                                'keys',
                                getpass.getuser() + '.wif')

    try:
        with open(filename, 'r') as key_file:
            wif_key = key_file.read().strip()
            signing_key = signing.encode_privkey(
                signing.decode_privkey(wif_key, 'wif'), 'hex')
            pubkey = signing.encode_pubkey(
                signing.generate_pubkey(signing_key), 'hex')

            return pubkey, signing_key
    except IOError as e:
        raise CliException('Unable to read key file: {}'.format(str(e)))


def _create_batch(pubkey, signing_key, transactions):
    """Creates a batch from a list of transactions and a public key, and signs
    the resulting batch with the given signing key.

    Args:
        pubkey (str): The public key associated with the signing key.
        signing_key (str): The private key for signing the batch.
        transactions (list of `Transaction`): The transactions to add to the
            batch.

    Returns:
        `Batch`: The constructed and signed batch.
    """
    txn_ids = [txn.header_signature for txn in transactions]
    batch_header = BatchHeader(signer_pubkey=pubkey,
                               transaction_ids=txn_ids).SerializeToString()

    return Batch(
        header=batch_header,
        header_signature=signing.sign(batch_header, signing_key),
        transactions=transactions
    )


def _create_propose_txn(pubkey, signing_key, setting_key_value):
    """Creates an individual sawtooth_config transaction for the given key and
    value.
    """
    setting_key, setting_value = setting_key_value
    nonce = str(datetime.datetime.utcnow().timestamp())
    proposal = ConfigProposal(
        setting=setting_key,
        value=setting_value,
        nonce=nonce)
    payload = ConfigPayload(data=proposal.SerializeToString(),
                            action=ConfigPayload.PROPOSE).SerializeToString()

    header = TransactionHeader(
        signer_pubkey=pubkey,
        family_name='sawtooth_config',
        family_version='1.0',
        inputs=_config_inputs(setting_key),
        outputs=_config_outputs(setting_key),
        dependencies=[],
        payload_encoding='application/protobuf',
        payload_sha512=hashlib.sha512(payload).hexdigest(),
        batcher_pubkey=pubkey
    ).SerializeToString()

    signature = signing.sign(header, signing_key)

    return Transaction(
        header=header,
        header_signature=signature,
        payload=payload)


def _config_inputs(key):
    """Creates the list of inputs for a sawtooth_config transaction, for a given
    setting key.
    """
    return [
        _key_to_address('sawtooth.config.authorization_type'),
        _key_to_address('sawtooth.config.vote.proposals'),
        _key_to_address('sawtooth.config.vote.authorized_keys'),
        _key_to_address('sawtooth.config.vote.approval_threshold'),
        _key_to_address(key)
    ]


def _config_outputs(key):
    """Creates the list of outputs for a sawtooth_config transaction, for a
    given setting key.
    """
    return [
        _key_to_address('sawtooth.config.vote.proposals'),
        _key_to_address(key)
    ]


def _key_to_address(key):
    """Creates the state address for a given setting key.
    """
    return '000000' + hashlib.sha256(key.encode()).hexdigest()
