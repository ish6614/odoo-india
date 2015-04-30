# -*- coding: utf-8 -*-
##############################################################################
#
#    OpenERP, Open Source Management Solution
#    Copyright (C) 2004-2010 Tiny SPRL (<http://tiny.be>).
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
import time
import openerp.netsvc

from openerp.osv import fields, osv
import openerp.addons.decimal_precision as dp
from openerp.tools.translate import _
from openerp.tools import DEFAULT_SERVER_DATETIME_FORMAT

class qc2reject(osv.osv_memory):
    _name = "qc2reject"
    _description = "QC to Reject"

    def default_get(self, cr, uid, fields, context=None):
        """
        -Process
            -Set default values of 
                -Active_id
                -Product
                -Total Qty
        """
        context = context or {}
        move_obj = self.pool.get('stock.move')
        to_qc_qty = context and context.get('to_qc_qty', 0.0) or 0.0
        product_id = context and context.get('product_id', False) or False
        move_id = context and context.get('active_id',False) or False
        already_rejected_qty =  context and context.get('already_rejected_qty',False) or False
        uom_id = False
        reason = ''
        if move_id:
            move = move_obj.browse(cr, uid, move_id)
            uom_id = move.product_uom and move.product_uom.id or False
            reason = move.note
        res = {}
        if 'product_id' in fields:
            res.update({'product_id': product_id})
        if 'to_qc_qty' in fields:
            res.update({'to_qc_qty': to_qc_qty})
        if 'already_rejected_qty' in fields:
            res.update({'already_rejected_qty': already_rejected_qty})
        if 'uom_id' in fields:
            res.update({'uom_id': uom_id})
        return res

    _columns = {
        'product_id': fields.many2one('product.product', 'Product', readonly=True),
        'to_qc_qty': fields.float('In QC Quantity', digits_compute=dp.get_precision('Product Unit of Measure'), readonly=True),
        'already_rejected_qty': fields.float('Already Reject Quantity', digits_compute=dp.get_precision('Product Unit of Measure'), readonly=True),
        'reject_qty': fields.float('Reject Quantity', digits_compute=dp.get_precision('Product Unit of Measure')),
        'uom_id': fields.many2one('product.uom', 'UoM', readonly=True),
        'reason':  fields.text('Reason')
    }



    def _check_validation_reject_qty(self, cr, uid, to_qc_qty, reject_qty, context=None):
        """
        - Process
            - Warning raise, if process qty > In qc qty or process qty < 0,
        """
        context = context or {}
        if reject_qty <= 0.0:
            raise osv.except_osv(_('Warning!'), _('Provide proper value of Reject Quantity (%s)' % (reject_qty)))
        if reject_qty > to_qc_qty:
            raise osv.except_osv(_('Reject Quantity over the limit!'), _('Reject Quantity(%s) greater then In QC Quantity(%s)' % (reject_qty, to_qc_qty)))
        return True

    def to_process_qty(self, cr, uid, ids, context=None):
        """
        - Process
            - Warning raise, Validation check for Accepted qty.
            - rejection qty overwrited on move with updated reason.
        """

        context = context or {}
        move_obj = self.pool.get('stock.move')
        move_id = context.get('active_id',False)

        wizard_rec = self.browse(cr, uid, ids[0])
        move_data = move_obj.browse(cr, uid, move_id)
        to_qc_qty = wizard_rec.to_qc_qty
        already_rejected_qty = wizard_rec.already_rejected_qty
        reject_qty = wizard_rec.reject_qty
        reason = move_data.note or ''
        reason += '\n\n'+ wizard_rec.reason

        self._check_validation_reject_qty(cr, uid, to_qc_qty, reject_qty, context=context)
        #update QC quantity to old move line
        #qc_completed== True if total == QC 
        total_rejected_qty = already_rejected_qty + reject_qty
        dict_to = {'qc_rejected_qty': total_rejected_qty, 'note':reason}
        if move_data.product_qty == to_qc_qty + total_rejected_qty:
            dict_to.update({'qc_completed':True})
        move_data.write(dict_to)
        return True

qc2reject()

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
