# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Business Applications
#    Copyright (C) 2004-2012 OpenERP S.A. (<http://openerp.com>).
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as
#    published by the Free Software Foundation, either version 3 of the
#    License, or (at your option) any later version.
#
#    This program is distributed in the hope that it will be useful,
#    but WITHOUT ANY WARRANTY; without even the implied warranty of
#    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#    GNU Affero General Public License for more details.
#
#    You should have received a copy of the GNU Affero General Public License
#    along with this program.  If not, see <http://www.gnu.org/licenses/>.
#
##############################################################################

import logging

from openerp.osv import fields, osv

_logger = logging.getLogger(__name__)

class indian_base_configuration(osv.osv_memory):
    _name = 'indian.base.config.settings'
    _inherit = 'res.config.settings'

    _columns = {
        'module_stock_indent': fields.boolean('Manage Internal material, service request through Indents.',
            help = """Allows you to keeps track of internal material request.
            It installs the stock_indent module."""),
        'module_stock_gatepass': fields.boolean('Track outgoing material through Gatepass',
            help = """Allows gate keeper to pass the outgoing materials, products, etc. and keeps track of returning items.
            It installs the stock_gatepass module."""),
        'group_cst_config':fields.boolean('Enable Central Sales Tax on Partners', implied_group='l10n_in_base.group_cst_config', help = """TODO"""),
        'group_excise_config':fields.boolean('Enable Excise Control Code on Partners', implied_group='l10n_in_base.group_excise_config', help = """TODO"""),
        'group_tin_config':fields.boolean('Enable Tax Identification Number on Partners', implied_group='l10n_in_base.group_tin_config', help = """TODO"""),
        'group_service_config':fields.boolean('Enable Service Tax Number on Partnere', implied_group='l10n_in_base.group_service_config', help = """TODO"""),
    }

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4: