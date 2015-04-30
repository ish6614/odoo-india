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
from openerp.tools.translate import _
import openerp.addons.decimal_precision as dp

class mrp_partially_close(osv.osv_memory):
    _name = "mrp.partially.close"

    def default_get(self, cr, uid, fields, context):
        """ 
        To get default values for the object.
        """
        prod_obj = self.pool.get('mrp.production')
        production_id = context and context.get('active_id', False) or False
        res = super(mrp_partially_close, self).default_get(cr, uid, fields, context=context)
        assert production_id, "Production Id should be specified in context as a Active ID."
        prod = prod_obj.browse(cr, uid, production_id, context=context)
        scrapped_qty = 0.0
        for wo in prod.workcenter_lines:
            for mrej in wo.moves_rejection:
                scrapped_qty += mrej.s_rejected_qty or 0.0

        already_produced_qty = prod.already_produced_qty
        if 'qty' in fields:
            res.update({'qty': prod.product_qty - (already_produced_qty + scrapped_qty)})
        if 'total_qty' in fields:
            res.update({'total_qty': prod.product_qty})
        if 'already_produced_qty' in fields:
            res.update({'already_produced_qty': already_produced_qty})
        if 'remain_qty' in fields:
            res.update({'remain_qty': prod.product_qty - (already_produced_qty + scrapped_qty)})
        if 'scraped_qty' in fields:
            res.update({'scraped_qty': scrapped_qty})
        return res

    _columns = {
        'qty': fields.float('Produce Quantity', digits_compute=dp.get_precision('Product Unit of Measure'), required=True),
        'total_qty': fields.float('Total Quantity', digits_compute=dp.get_precision('Product Unit of Measure')),
        'scraped_qty': fields.float('Scrap Quantity', digits_compute=dp.get_precision('Product Unit of Measure')),
        'already_produced_qty': fields.float('Already Produced Quantity', digits_compute=dp.get_precision('Product Unit of Measure')),
        'remain_qty': fields.float('Remain Produce Quantity', digits_compute=dp.get_precision('Product Unit of Measure')),
    }

#    def onchange_qty(self, cr, uid, ids, scraped_qty, remain_qty, qty, context=None):
#        """
#        Process
#            - To update scraped quantity.
#        """
#        return {'value':{'scraped_qty': remain_qty - qty}}

    def _prepare_order_line_move(self, cr, uid, production, picking_id, scrap_qty, context=None):
        """
        -Process
            -create scrap move from stock
                Source Location : Store
                Destination Location: Scrap
        """
        location_obj = self.pool.get('stock.location')
        scrap_location_ids = location_obj.search(cr, uid, [('scrap_location', '=', True)], context=context)
        if not scrap_location_ids:
            raise osv.except_osv(_('Scrap Location not found!'), _('Atleast define one location for scrap.'))
        return {
            'name': production.name,
            'picking_id': picking_id,
            'product_id': production.product_id.id,
            'date': time.strftime('%Y-%m-%d %H:%M:%S'),
            'date_expected': time.strftime('%Y-%m-%d %H:%M:%S'),
            'product_qty': scrap_qty,
            'product_uom': production.product_uom.id,
            'product_uos_qty': scrap_qty,
            'product_uos': production.product_uom.id,
            'location_id': production.location_dest_id.id,
            'location_dest_id': scrap_location_ids[0],
            'tracking_id': False,
            'state': 'draft',
            'company_id': production.company_id.id,
            'price_unit': production.product_id.standard_price or 0.0
        }

    def _prepare_order_picking(self, cr, uid, production, scrap_qty, context=None):
        """
        -Process
            -create Picking for scrap move
        """
        pick_name = self.pool.get('ir.sequence').get(cr, uid, 'stock.picking')
        return {
            'name': pick_name,
            'origin': production.name,
            'date': time.strftime('%Y-%m-%d %H:%M:%S'),
            'type': 'internal',
            'state': 'draft',
            'move_type': 'one',
            'note': 'Scrap Order:-' + production.product_id.name + ':' + str(scrap_qty) + ':' + production.product_uom.name,
            'invoice_state': 'none',
            'company_id': production.company_id.id,
        }

    def do_produce(self, cr, uid, ids, context=None):
        """
        Process
            -Pass remain production qty to action_produce method with consume_produce mode.
            -generate scrap order
            -attached scrap order to production order
            -attached scrap quantity to production order
        """
        prod_obj = self.pool.get('mrp.production')
        pick_obj = self.pool.get('stock.picking')
        move_obj = self.pool.get('stock.move')
        wf_service = netsvc.LocalService("workflow")
        production_id = context.get('active_id', False)
        assert production_id, "Production Id should be specified in context as a Active ID."
        wizard = self.browse(cr, uid, ids[0], context=context)

        prod = prod_obj.browse(cr, uid, production_id, context=context)
        remain_qty = prod.product_qty - (prod.already_produced_qty + prod.scraped_qty)

        partially_qty = wizard.qty
        #cannot zero ;)
        if partially_qty < 0.0:
            raise osv.except_osv(_('Warning!'), _('Provide proper value of partially qty(%s)' % (partially_qty)))
        if partially_qty > remain_qty:
            raise osv.except_osv(_('Over Limit Quantity!'), _('Wizard partially quantity() is greater then remaining quantity(%s)' % (partially_qty, remain_qty)))

        self.pool.get('mrp.production').action_produce(cr, uid, production_id, remain_qty, 'consume_produce', context=context)

        scrap_qty = remain_qty - partially_qty
        #Scrap order will be generate only if having scrap quantity.
        if scrap_qty > 0:
            picking_id = pick_obj.create(cr, uid, self._prepare_order_picking(cr, uid, prod, scrap_qty, context=context), context=context)
            move_obj.create(cr, uid, self._prepare_order_line_move(cr, uid, prod, picking_id, scrap_qty, context=context), context=context)
            prod.write({'scrap_order_id':picking_id, 'scraped_qty': scrap_qty}) 


            #Picking Directly Done
            wf_service.trg_validate(uid, 'stock.picking', picking_id, 'button_confirm', cr)
            pick_obj.action_move(cr, uid, [picking_id], context)
            wf_service.trg_validate(uid, 'stock.picking', picking_id, 'button_done', cr)

        return {}

mrp_partially_close()

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
