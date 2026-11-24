import os
from pathlib import Path

from flask import Flask,jsonify

from app.api import api
from app.faults import LedgerError
from app.ledger.bookshelf import Bookshelf
from app.rework import ReworkDesk, seed_from_job
from app.web import pages


def _seed_rework():
    from app.job import MixingJob
    from app.receipts.packet import read_sample
    return seed_from_job(MixingJob(read_sample('dirty')))


def create_app(path=None):
    service=Flask(__name__)
    service.url_map.strict_slashes=False
    service.jinja_env.autoescape=True
    service.config['MAX_CONTENT_LENGTH']=5*1024*1024
    shelf_path=Path(path or os.getenv('PALETTE_SHELF','var/receipts'))
    service.extensions['receipt_bookshelf']=Bookshelf(shelf_path)
    service.extensions['rework_desk']=ReworkDesk(shelf_path/'rework-state.json',seed=_seed_rework)
    service.register_blueprint(api)
    service.register_blueprint(pages)

    @service.errorhandler(LedgerError)
    def bad(error):
        response = {'code': error.code, 'error': error.code, 'reason': error.code,
                    'message': str(error)}
        codes = getattr(error, 'codes', None)
        if codes:
            response['codes'] = list(codes)
        return jsonify(response),error.status

    @service.errorhandler(413)
    def oversized(_):
        return jsonify(code='PACKET_LIMIT',message='表单包超过 5 MiB'),413

    return service
