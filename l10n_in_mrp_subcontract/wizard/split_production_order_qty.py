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
import openerp.netsvc

from openerp.osv import fields, osv, orm
import openerp.addons.decimal_precision as dp
from openerp.tools.translate import _
from openerp import tools

class split_production_order_qty(osv.osv_memory):
    _name = "split.production.order.qty"
    _description = "Change Product Quantity"
    _columns = {
        'product_id' : fields.many2one('product.product', 'Product', readonly=True),
        'qty': fields.float('Split Order Quantity', digits_compute=dp.get_precision('Product Unit of Measure'), required=True, help='This quantity is expressed in the Default Unit of Measure of the product.'),
    }

    def default_get(self, cr, uid, fields, context):
        """
        Process
            -To set default quantity
        """
        product_id = context and context.get('product_id', False) or False
        total_qty = context and context.get('total_qty', False) or False
        res = super(split_production_order_qty, self).default_get(cr, uid, fields, context=context)

        if 'product_id' in fields:
            res.update({'product_id': product_id})
        if 'qty' in fields:
            res.update({'qty': total_qty})
        return res

    def split_qty(self, cr, uid, ids, context=None):
        """
        Process
            -Split Production order with partial Quantity
            -Update old Production order
        """
        if context is None:
            context = {}

        production_id = context and context.get('active_id', False)
        assert production_id, _('Production ID is not set in Context')

        prod_obj = self.pool.get('mrp.production')
        uom_obj = self.pool.get('product.uom')
        wf_service = netsvc.LocalService("workflow")

        wizard_rec = self.browse(cr, uid, ids[0], context=context)
        update_qty = wizard_rec.qty
        prod_rec = prod_obj.browse(cr, uid, production_id, context=context)
        state = prod_rec.state
        mvd_2_wrkctr = prod_rec.moves_to_workorder

        if state not in ('ready','split_order') or mvd_2_wrkctr:
            raise osv.except_osv(_('Cannot Split!'), _('You cannot split order.\n1)Products already moved into workcenter.2)Production not in "Ready to Produce" or "Split Order" state.'))
        if update_qty < 0:
            raise osv.except_osv(_('Warning!'), _('Quantity cannot be negative.'))
        if update_qty >= prod_rec.product_qty:
            raise osv.except_osv(_('Warning!'), _('Split quantity(%s) cannot greater then or equal to real quantity(%s).'%(update_qty,prod_rec.product_qty)))

        context.update({'split_qty':True})
        original_qty = prod_rec.product_qty

        man_dict = {
                    'name': self.pool.get('ir.sequence').get(cr, uid, 'mrp.production'),
                    'origin': prod_rec.origin,
                    'product_id': prod_rec.product_id.id,
                    'product_qty': update_qty,
                    'parent_id':production_id,
                    'product_uom': prod_rec.product_uom.id,
                    'product_uos_qty': prod_rec.product_uos_qty,
                    'product_uos': prod_rec.product_uos and prod_rec.product_uos.id or False,
                    'location_src_id': prod_rec.location_src_id.id,
                    'location_dest_id': prod_rec.location_dest_id.id,
                    'bom_id': prod_rec.bom_id and prod_rec.bom_id.id or False,
                    'date_planned': prod_rec.date_planned,
                    'company_id': prod_rec.company_id.id,
                    'customer_id': prod_rec.customer_id and prod_rec.customer_id.id or False,
                    'sale_order_id': prod_rec.sale_order_id and prod_rec.sale_order_id.id or False,
                    'state':'draft'
                    }
        new_id = prod_obj.create(cr, uid, man_dict)
        prod_rec.write({'product_qty': original_qty - update_qty})
        if state == 'ready':
            wf_service.trg_validate(uid, 'mrp.production', new_id, 'button_confirm', cr)
            prod_obj.force_production(cr, uid, [new_id])
            for mvl in prod_rec.move_lines:
                factor = float(mvl.product_qty) / (float(original_qty) or 1.0)
                nxt_mv_qty = update_qty * factor
                mvl.write({'product_qty': mvl.product_qty - nxt_mv_qty,'product_uos_qty': uom_obj._compute_qty(cr, uid, mvl.product_uom.id, mvl.product_qty - nxt_mv_qty, mvl.product_uos.id),})
        return {}
    
split_production_order_qty()
# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
