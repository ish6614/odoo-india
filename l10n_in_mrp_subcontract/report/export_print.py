#-*- coding:utf-8 -*-

##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2009 Tiny SPRL (<http://tiny.be>). All Rights Reserved
#    d$
#
#    This program is free software: you can redistribute it and/or modify
#    it under the terms of the GNU Affero General Public License as published by
#    the Free Software Foundation, either version 3 of the License, or
#    (at your option) any later version.
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

import time
from openerp.report import report_sxw
from operator import itemgetter 
import openerp.pooler

class export_print(report_sxw.rml_parse):
    def __init__(self, cr, uid, name, context):
        super(export_print, self).__init__(cr, uid, name, context=context)
        self.localcontext.update({
            'time': time,
            'get_lines':self._get_lines,
            'g_add':self._g_add
        })

    def _g_add(self,address):
        # get the information that will be injected into the display format
        # get the address format
        address_format = "%(name)s\n%(street)s\n%(street2)s\n%(city)s %(state_code)s %(zip)s\n%(country_name)s"
        args = {
            'state_code': address.state_id and address.state_id.code or '',
            'state_name': address.state_id and address.state_id.name or '',
            'country_code': address.country_id and address.country_id.code or '',
            'country_name': address.country_id and address.country_id.name or '',
            'company_name': address.parent_id and address.parent_id.name or '',
        }
        for field in ('name','street', 'street2', 'zip', 'city', 'state_id', 'country_id'):
            args[field] = getattr(address, field) or ''
        address_format = '%(company_name)s\n' + address_format
        return address_format % args

    def _get_lines(self, form):
        cr = self.cr
        uid = self.uid
        lines = []
        data = self.pool.get('export.line').browse(cr, uid, form['line_ids'])
        pg_brk_cunt = 0
        count = len(data)
        print "count",count
        for ln in data:
            action = 'nobreak'
            pg_brk_cunt += 1
            lines.append({'code': ln.product_id.default_code or '', 'qty':ln.qty,'net_wt':ln.net_wt,'gross_wt':ln.gross_wt,'action':action,'count':count})
            pg_brk_cunt += 1
            lines.append({'code': ln.product_id.name or '', 'qty':'','net_wt':'','gross_wt':'','action':action,'count':count})
            pg_brk_cunt += 1
            if pg_brk_cunt % 18 == 0: action = 'break'
            lines.append({'code': ln.part_no_rev and 'Part Number Revision: '+str(ln.part_no_rev) or 'Part Number Revision:', 'qty':'','net_wt':'','gross_wt':'','action':action,'count':count})
        return lines

report_sxw.report_sxw('report.export.do.order', 'export.report', 'l10n_in_mrp_subcontract/report/export_report.rml', parser=export_print, header=False)

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
