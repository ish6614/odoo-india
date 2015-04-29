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
from openerp import netsvc

from openerp.osv import osv, fields
from openerp.tools.translate import _
from openerp import SUPERUSER_ID
import openerp.addons.decimal_precision as dp
from openerp.tools import DEFAULT_SERVER_DATETIME_FORMAT
from datetime import datetime
from dateutil.relativedelta import relativedelta

class purchase_expected_date(osv.osv):
    _name = 'purchase.expected.date'
    _columns = {
        'product_id': fields.many2one('product.product', 'Product'),
        'production_id':  fields.many2one('mrp.production', 'Production Order'),
        'order_id':  fields.many2one('purchase.order', 'Purchase Order'),
        'production_scheduled_date':  fields.date('Production Scheduled Date'),
        'purchase_lead_time':  fields.integer('Product Purchase Lead Time'),
        'company_po_lead_time':  fields.integer('Company Purchase Lead Time'),
        'po_expected_date':  fields.date('Purchase Expected Date')
    }

purchase_expected_date()

class purchase_order(osv.osv):
    _inherit = 'purchase.order'

    def _make_expected_date_data(self, cr, uid, data, order_id, company_id , context=None):
        """ create product wise dictionary"""
        total_list = []
        for rec in data:
            if rec.get('product_id') and rec.get('production_id') and rec.get('move_id'):
                prod_obj = self.pool.get('mrp.production')
                product_obj = self.pool.get('product.product')
                comp_obj = self.pool.get('res.company')
                date_planned = prod_obj.browse(cr, uid, rec['production_id']).date_planned
                po_lead_time = product_obj.browse(cr, uid, rec['product_id']).seller_delay
                comp_lead_time = comp_obj.browse(cr, uid, company_id).po_lead
                scheduled_date = datetime.strptime(date_planned, DEFAULT_SERVER_DATETIME_FORMAT)
                po_expected_date = (scheduled_date - relativedelta(days=(int(po_lead_time) + int(comp_lead_time))) )
                total_list.append(
                                    {
                                    'product_id': int(rec['product_id']),
                                    'production_id': int(rec['production_id']),
                                    'production_scheduled_date': scheduled_date.strftime('%Y-%m-%d'),
                                    'purchase_lead_time':  int(po_lead_time),
                                    'po_expected_date': po_expected_date.strftime('%Y-%m-%d'),
                                    'company_po_lead_time': int(comp_lead_time),
                                    'order_id':order_id
                                     }
                                  )
        return total_list

    def _get_expected_dates_by_products(self, cr, uid, ids,context=None):
        """ Expected date Products wise"""
        res = {}
        context = context or {}
        if not ids: return res
        po_expd_obj = self.pool.get('purchase.expected.date')

        for order in self.browse(cr, uid, ids, context=context):
            unlink_ids = [x.id for x in order.expected_date_by_production_order]
            po_expd_obj.unlink(cr, SUPERUSER_ID, unlink_ids, context=context)

        line_data = []
        for order in self.browse(cr, uid, ids, context=context):
            if order.state not in ('draft'):
                return res
            produced_p_ids = [] 
            for line in order.order_line:
                if not line.product_id: pass
                if line.product_id and line.product_id.supply_method <> 'produce': pass
                produced_p_ids.append(line.product_id.id)

            produced_p_ids = list(set(produced_p_ids))
            if produced_p_ids:
                cr.execute("""
                            SELECT sm.product_id,mpm.production_id,mpm.move_id from mrp_production_move_ids mpm 
                            LEFT JOIN mrp_production mp on (mp.id = mpm.production_id) 
                            LEFT JOIN stock_move sm on (sm.id = mpm.move_id) 
                            WHERE sm.product_id IN %s 
                            AND sm.state not in ('done','cancel') 
                            AND mp.state in ('confirmed') 
                """, (tuple(produced_p_ids),))

                data = cr.dictfetchall()
                line_data = self._make_expected_date_data(cr, uid, data, order.id, order.company_id and order.company_id.id or 1)
            #create date lines on order
            for c_line in line_data:
                po_expd_obj.create(cr, uid, c_line,context=context)
        return True

    def _amount_all(self, cr, uid, ids, field_name, arg, context=None):
        cur_obj=self.pool.get('res.currency')
        tax_obj = self.pool.get('account.tax')
        
        res = {}
        untax_amount = 0
        
        for order in self.browse(cr, uid, ids, context=context):
            res[order.id] = {
                'amount_untaxed': 0.0,
                'amount_tax': 0.0,
                'amount_total': 0.0,
                'other_charges':0.0,
            }
            order_total = val = val1 = tax_total = other_charges = included_price = 0.0
            cur = order.pricelist_id.currency_id
            for line in order.order_line:
                order_total += line.price_subtotal

            for line in order.order_line:
                #price = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
                price = line.purchase_unit_rate * (1 - (line.discount or 0.0) / 100.0)
                val1 += (price * line.line_qty)
                untax_amount += line.price_subtotal
                if order.package_and_forwording_type == 'per_unit' and order.package_and_forwording:
                    other_charges += (order.package_and_forwording * line.line_qty)
                if order.freight_type == 'per_unit' and order.freight:
                    other_charges += (order.freight * line.line_qty)
                if order_total > 0:
                    #Add fixed amount to order included in price
                    pre_line = round((price * 100) / order_total,2)
                    line_part = 0.0
                    if order.package_and_forwording_type == 'include' and order.package_and_forwording:
                        line_part = order.package_and_forwording * (pre_line / 100)
                        price -= line_part
                        
                    if order.freight_type == 'include' and order.freight:
                        line_part = order.freight  * (pre_line / 100)
                        price -= line_part
                        
                    if order.insurance_type == 'include' and order.insurance:
                        line_part = order.insurance  * (pre_line / 100)
                        price -= line_part

                taxes = tax_obj.compute_all(cr, uid, line.taxes_id, price, line.line_qty, line.product_id, line.order_id.partner_id)
                tax_total += taxes.get('total_included', 0.0) - taxes.get('total', 0.0)
            
            #Add fixed amount to order included in price
            if order.package_and_forwording_type == 'include' and order.package_and_forwording:
                included_price += order.package_and_forwording
                order_total -= order.package_and_forwording
                
            if order.freight_type == 'include' and order.freight:
                included_price += order.freight
                order_total -= order.freight
                
            if order.insurance_type == 'include' and order.insurance:
                included_price += order.insurance
                order_total -= order.insurance
            
            if order_total > 0:
                #Add fixed amount to order percentage
                if order.package_and_forwording_type == 'percentage' and order.package_and_forwording:
                    other_charges += order_total * (order.package_and_forwording / 100)
                
                if order.freight_type == 'percentage' and order.freight:
                    other_charges += order_total * (order.freight / 100)
                    
                if order.insurance_type == 'percentage' and order.insurance:
                    other_charges += order_total * (order.insurance/100)

            #Add fixed amount to order untax_amount
            if order.package_and_forwording_type in ('fix', 'include') and order.package_and_forwording:
                other_charges += order.package_and_forwording
            if order.freight_type in ('fix', 'include') and order.freight:
                other_charges += order.freight
            if order.insurance_type in ('fix', 'include') and order.insurance:
                other_charges += order.insurance

            tax_total = cur_obj.round(cr, uid, cur, tax_total)
            untax_amount = cur_obj.round(cr, uid, cur, untax_amount)  - included_price
            order_total = other_charges + tax_total + untax_amount + order.round_off
            res[order.id]['amount_tax'] = tax_total
            res[order.id]['amount_untaxed'] = untax_amount
            res[order.id]['other_charges'] = other_charges
            res[order.id]['amount_total'] = order_total
        return res

    def _get_order(self, cr, uid, ids, context=None):
        result = {}
        for line in self.pool.get('purchase.order.line').browse(cr, uid, ids, context=context):
            result[line.order_id.id] = True
        return result.keys()

    def action_po_amendment(self, cr, uid, ids, context=None):
        """
        -Process: Purchase Order Amendment in one step
            -Cancel picking First
            -Cancel Purchase Order
            -Reset to draft state
            -Update that po as is_amendment True(Because to stop purchase order line onchange)
        """
        pickin_obj = self.pool.get('stock.picking.in')
        wf_service = netsvc.LocalService("workflow")
        if not ids: return True

        picking_ids = pickin_obj.search(cr, uid, [('purchase_id', '=', ids[0])])
        pickin_check = pickin_obj.read(cr, uid,picking_ids,['state'],context=context)
        if 'done' in [x['state'] for x in pickin_check]:
            raise osv.except_osv(_('Cannot Amendment!'), _('You have already received the Inward for this purchase order.'))

        pickids_2_cancel = [x['id'] for x in pickin_check if x['state'] != 'cancel']
        for cancel in pickids_2_cancel:
            wf_service.trg_validate(uid, 'stock.picking', cancel, 'button_cancel', cr)

        wf_service.trg_validate(uid, 'purchase.order', ids[0], 'purchase_cancel', cr)
        self.action_cancel_draft(cr, uid, ids, context=context)
        self.message_post(cr, uid, ids, body=_("In Amendment Process"), context=context)
        self.write(cr, uid, ids, {'is_amendment':True}, context=context)

        return True

    _columns = {
        'service_order': fields.boolean('Service Order'),
        'is_amendment': fields.boolean('Is Amendment?'),
        'workorder_id':  fields.many2one('mrp.production.workcenter.line', 'Work-Order'),
        'service_delivery_order':  fields.many2one('stock.picking', 'Service Delivery Order'),
        'expected_date_by_production_order': fields.one2many('purchase.expected.date', 'order_id',string='Expected Dates By Production Order',readonly=True),

        'amount_untaxed': fields.function(_amount_all, digits_compute= dp.get_precision('Account'), string='Untaxed Amount',
            store={
                   'purchase.order': (lambda self, cr, uid, ids, c={}: ids, ['round_off','insurance', 'insurance_type', 'freight_type', 'freight', 'package_and_forwording_type', 'package_and_forwording', 'order_line'], 11),
                   'purchase.order.line': (_get_order, None, 10),
            }, multi="sums", help="The amount without tax", track_visibility='always'),
        'amount_tax': fields.function(_amount_all, digits_compute= dp.get_precision('Account'), string='Taxes',
            store={
                'purchase.order': (lambda self, cr, uid, ids, c={}: ids, ['round_off','insurance', 'insurance_type', 'freight_type', 'freight', 'package_and_forwording_type', 'package_and_forwording', 'order_line'], 11),
                'purchase.order.line': (_get_order, None, 10),
            }, multi="sums", help="The tax amount"),
        'amount_total': fields.function(_amount_all, digits_compute= dp.get_precision('Account'), string='Total',
            store={
                'purchase.order': (lambda self, cr, uid, ids, c={}: ids, ['round_off','insurance', 'insurance_type', 'freight_type', 'freight', 'package_and_forwording_type', 'package_and_forwording', 'order_line'], 11),
                'purchase.order.line': (_get_order, None, 10),
            }, multi="sums",help="The total amount"),
        'other_charges': fields.function(_amount_all, digits_compute=dp.get_precision('Account'), string='Other Charges',
            store={
                'purchase.order': (lambda self, cr, uid, ids, c={}: ids, ['round_off','insurance', 'insurance_type', 'freight_type', 'freight', 'package_and_forwording_type', 'package_and_forwording', 'order_line'], 11),
                'purchase.order.line': (_get_order, None, 10),
            }, multi="sums", help="Computed as Packing & Forwarding + Freight + Insurance"),

    }

    def create(self, cr, uid, vals, context=None):
        """ To update Expected Date lines"""
        context = context or {}
        new_id = super(purchase_order,self).create(cr, uid, vals,context=context)
        self._get_expected_dates_by_products(cr, uid, [new_id], context=context)
        return new_id

    def write(self, cr, uid, ids, vals, context=None):
        """ To update Expected Date lines"""
        context = context or {}
        res = super(purchase_order,self).write(cr, uid, ids, vals,context=context)
        if isinstance(ids,int):
            ids = [ids]
        if vals.get('order_line') or vals.get('state'):
            self._get_expected_dates_by_products(cr, uid, ids, context=context)
        return res

    def copy(self, cr, uid, id, default=None, context=None):
        if not default:
            default = {}
        default.update({
            'service_order': False,
            'workorder_id': False,
            'service_delivery_order': False,
        })
        return super(purchase_order, self).copy(cr, uid, id, default, context)

    def _check_warehouse_input_stock(self, cr, uid, order):
        """
        -Process
            -Warehouse, Stock location == input location, as it is
            otherwise
                -Warehouse stock location <> Purchase order destination location, as it is,
                or 
                -Warehouse input location <> Purchase order destination location, as it is,

                otherwise
                    -Process flow change
                        -Moves create from Supplier --> Stock input locations, instead of Supplier --> Order destination locations
        """
        stock_location_id = order.warehouse_id.lot_stock_id.id
        input_location_id = order.warehouse_id.lot_input_id.id
        location_id = order.location_id.id
        pass_to_qc = False
        #if (stock_location_id != input_location_id and stock_location_id == location_id) or (stock_location_id != input_location_id and input_location_id == location_id):
        if stock_location_id != input_location_id and input_location_id != location_id:
            location_id = input_location_id
            pass_to_qc = True
        return location_id, pass_to_qc

    def _prepare_order_line_move(self, cr, uid, order, order_line, picking_id, context=None):
        """
        -Process
            -call super method to get dictonary of moves
            -find and check stock input and stock id match or not?
                - If Its match then as it is flow,
                otherwise
                -moves transfer first to QC location(stock input) location then it will be transfered to stock location
        """
        res = super(purchase_order, self)._prepare_order_line_move(cr, uid, order, order_line, picking_id, context=context)
        location_id, pass_to_qc = self._check_warehouse_input_stock(cr, uid, order)
        res.update({'location_dest_id': location_id,'is_qc':pass_to_qc,'purchase_qty':order_line.line_qty or 0.0,'purchase_uom_id':order_line.line_uom_id and order_line.line_uom_id.id or False})
        return res

    def _prepare_order_picking(self, cr, uid, order, context=None):
        """
        -Process
            -call super method to get dictonary of moves
            -find and check stock input and stock id match or not?
                - If Its match then as it is flow,
                otherwise
                -Pass to Quality Control or Not ?
        """
        res = super(purchase_order, self)._prepare_order_picking(cr, uid, order, context=context)
        location_id, pass_to_qc = self._check_warehouse_input_stock(cr, uid, order)
        res.update({'pass_to_qc': pass_to_qc,'move_loc_id': order.location_id.id,'qc_loc_id':location_id})
        return res

    def _prepare_inv_line(self, cr, uid, account_id, order_line, context=None):
        """
        -Process
            -call super()
            -pass the purchase qty and Purchase Uom to invoice line
        """
        res = super(purchase_order, self)._prepare_inv_line(cr, uid, account_id, order_line, context=context)
        res.update({
                    'quantity':order_line.line_qty or 0.0,
                    'price_unit':order_line.purchase_unit_rate,
                    'uos_id': order_line.line_uom_id and order_line.line_uom_id.id or False,
                    'pur_line_qty': order_line.product_qty or 0.0,
                    'pur_line_uom_id': order_line.product_uom.id or False,
                    })
        return res

    def onchange_warehouse_id(self, cr, uid, ids, warehouse_id):
        """
        -Process
            -update location on create purchase order = stock location instead of input location
        """
        if not warehouse_id:
            return {}
        warehouse = self.pool.get('stock.warehouse').browse(cr, uid, warehouse_id)
        # update lot_stock_id instead of lot_input_id
        return {'value':{'location_id': warehouse.lot_stock_id.id, 'dest_address_id': False}}

    def _create_delivery_picking(self, cr, uid, order, context=None):
        """
        Process
            -Create Delivery Picking for outsource
        Return
            -Dictionary of picking
        """
        pick_name = self.pool.get('ir.sequence').get(cr, uid, 'stock.picking.out')
        production = order.workorder_id.production_id
        return {
            'name': pick_name,
            'origin': production.name,
            'date': time.strftime(DEFAULT_SERVER_DATETIME_FORMAT),
            'type': 'out',
            'state': 'draft',
            'partner_id': order.partner_id.id,
            'note': order.notes,
            'invoice_state': 'none',
            'company_id': order.company_id.id,
            'service_order':True,
            'workorder_id': order.workorder_id.id,
        }

    def _create_delivery_move(self, cr , uid, order, deliver_id, context=None):
        """
        Process
            -Create move for outsource
                From : Production Location
                To : Supplier Location
        Return
            -Dictionary of move
        """
        context = context or {}
        uom_obj = self.pool.get('product.uom')
        if not order.workorder_id:
            raise osv.except_osv(_('Warning!'), _('Can you check again, is this service order??!'))
        production = order.workorder_id.production_id

        #Delivery Order Locations
        source_location_id = production.product_id.property_stock_production.id
        dest_location_id = order.partner_id.property_stock_supplier.id
        list_data = []
        for l in order.order_line:
            if l.product_id and l.product_id.type <> 'service':


                if l.line_uom_id:
                    uom_data = uom_obj.browse(cr, uid, l.line_uom_id.id).name
                    uom_char = uom_data and '/'+ str(uom_data) or ''

                list_data.append(
                                    {
                                        'name': l.product_id.name,
                                        'picking_id': deliver_id,
                                        'product_id': l.product_id.id,
                                        'date': time.strftime(DEFAULT_SERVER_DATETIME_FORMAT),
                                        'date_expected': time.strftime(DEFAULT_SERVER_DATETIME_FORMAT),
                                        'product_qty': l.product_qty or 0.0,
                                        'product_uom': l.product_id.uom_id.id,
                                        'product_uos_qty': l.product_qty or 0.0,
                                        'product_uos': l.product_id.uom_id.id,
                                        'partner_id': order.partner_id.id,
                                        'location_id': source_location_id,
                                        'location_dest_id': dest_location_id,
                                        'tracking_id': False,
                                        'state': 'draft',
                                        'company_id': production.company_id.id,
                                        'price_unit': l.product_id.standard_price or 0.0,

                                        'srvc_ordr_qty':l.line_qty,
                                        'srvc_ordr_uom':l.line_uom_id and l.line_uom_id.id or False,
                                        'uom_char':uom_char,
                                    }
                                 )
        return list_data

    def action_picking_create(self, cr, uid, ids, context=None):
        """
        -Process
            -super call()
            -if service order then at the time of confirmation , it will generate Delivery order from production.
        """
        out_pick_obj = self.pool.get('stock.picking.out')
        move_obj = self.pool.get('stock.move')
        wf_service = netsvc.LocalService("workflow")
        res = super(purchase_order,self).action_picking_create(cr, uid, ids, context=context)
        for order in self.browse(cr, uid, ids):
            if order.service_order:
                # Create Delivery Order
                delivery_order_id = out_pick_obj.create(cr, uid, self._create_delivery_picking(cr, uid, order, context=context), context=context)
                # Create Move
                move_lines = self._create_delivery_move(cr, uid, order, delivery_order_id, context=context)
                for move in move_lines:
                    move_obj.create(cr, uid, move, context=context)

                #Picking Directly Done
                wf_service.trg_validate(uid, 'stock.picking', delivery_order_id, 'button_confirm', cr)
                out_pick_obj.action_move(cr, uid, [delivery_order_id], context)
                wf_service.trg_validate(uid, 'stock.picking', delivery_order_id, 'button_done', cr)

                order.write({'service_delivery_order': delivery_order_id})
                
        return res

    def other_charges(self, cr, uid, order):
        """
        Process
            -Calculate pakaging & forwarding, freight and insurance charges 
        """
        tax_obj = self.pool.get('account.tax')
        untax_amount = 0

        order_total = val1 = tax_total = included_price = 0.0
        pkg_frwrd = freight = insurance = 0.0

        for line in order.order_line:
            order_total += line.price_subtotal

        for line in order.order_line:
            #price = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
            price = line.purchase_unit_rate * (1 - (line.discount or 0.0) / 100.0)
            val1 += (price * line.line_qty)
            untax_amount += line.price_subtotal

            if order.package_and_forwording_type == 'per_unit' and order.package_and_forwording:
                pkg_frwrd += (order.package_and_forwording * line.line_qty)
            if order.freight_type == 'per_unit' and order.freight:
                freight += (order.freight * line.line_qty)

            if order_total > 0:
                #Add fixed amount to order included in price
                pre_line = round((price * 100) / order_total,2)
                line_part = 0.0
                if order.package_and_forwording_type == 'include' and order.package_and_forwording:
                    line_part = order.package_and_forwording * (pre_line / 100)
                    price -= line_part

                if order.freight_type == 'include' and order.freight:
                    line_part = order.freight  * (pre_line / 100)
                    price -= line_part

                if order.insurance_type == 'include' and order.insurance:
                    line_part = order.insurance  * (pre_line / 100)
                    price -= line_part

            taxes = tax_obj.compute_all(cr, uid, line.taxes_id, price, line.line_qty, line.product_id, line.order_id.partner_id)
            tax_total += taxes.get('total_included', 0.0) - taxes.get('total', 0.0)

        #Add fixed amount to order included in price
        if order.package_and_forwording_type == 'include' and order.package_and_forwording:
            included_price += order.package_and_forwording
            order_total -= order.package_and_forwording

        if order.freight_type == 'include' and order.freight:
            included_price += order.freight
            order_total -= order.freight

        if order.insurance_type == 'include' and order.insurance:
            included_price += order.insurance
            order_total -= order.insurance

        if order_total > 0:
            #Add fixed amount to order percentage
            if order.package_and_forwording_type == 'percentage' and order.package_and_forwording:
                pkg_frwrd += order_total * (order.package_and_forwording / 100)

            if order.freight_type == 'percentage' and order.freight:
                freight += order_total * (order.freight / 100)

            if order.insurance_type == 'percentage' and order.insurance:
                insurance += order_total * (order.insurance/100)

        #Add fixed amount to order untax_amount
        if order.package_and_forwording_type in ('fix', 'include') and order.package_and_forwording:
            pkg_frwrd += order.package_and_forwording
        if order.freight_type in ('fix', 'include') and order.freight:
            freight += order.freight
        if order.insurance_type in ('fix', 'include') and order.insurance:
            insurance += order.insurance

        return pkg_frwrd,freight,insurance

    def action_invoice_create(self, cr, uid, ids, context=None):
        """Generates invoice for given ids of purchase orders and links that invoice ID to purchase order.
        :param ids: list of ids of purchase orders.
        :return: ID of created invoice.
        :rtype: int
        Process:
            This function overwrite only to find GRN Document No. and attached with related invoice.
        """
        if context is None:
            context = {}
        journal_obj = self.pool.get('account.journal')
        inv_obj = self.pool.get('account.invoice')
        inv_line_obj = self.pool.get('account.invoice.line')

        res = False
        uid_company_id = self.pool.get('res.users').browse(cr, uid, uid, context=context).company_id.id
        for order in self.browse(cr, uid, ids, context=context):
            payment_ref = ''#Added payment refenrece as GRN DOCUMENT NO.
            if order.picking_ids: payment_ref = order.picking_ids[0].name
            context.pop('force_company', None)
            if order.company_id.id != uid_company_id:
                #if the company of the document is different than the current user company, force the company in the context
                #then re-do a browse to read the property fields for the good company.
                context['force_company'] = order.company_id.id
                order = self.browse(cr, uid, order.id, context=context)
            pay_acc_id = order.partner_id.property_account_payable.id
            journal_ids = journal_obj.search(cr, uid, [('type', '=', 'purchase'), ('company_id', '=', order.company_id.id)], limit=1)
            if not journal_ids:
                raise osv.except_osv(_('Error!'),
                    _('Define purchase journal for this company: "%s" (id:%d).') % (order.company_id.name, order.company_id.id))

            # generate invoice line correspond to PO line and link that to created invoice (inv_id) and PO line
            inv_lines = []
            for po_line in order.order_line:
                acc_id = self._choose_account_from_po_line(cr, uid, po_line, context=context)
                inv_line_data = self._prepare_inv_line(cr, uid, acc_id, po_line, context=context)
                inv_line_id = inv_line_obj.create(cr, uid, inv_line_data, context=context)
                inv_lines.append(inv_line_id)

                po_line.write({'invoice_lines': [(4, inv_line_id)]}, context=context)

            #method to get all extra charges individualy
            pkg_frwrd, freight, insurance = self.other_charges(cr, uid, order)

            # get invoice data and create invoice
            inv_data = {
                'name': order.partner_ref or order.name,
                'reference': payment_ref or '',
                'account_id': pay_acc_id,
                'type': 'in_invoice',
                'partner_id': order.partner_id.id,
                'currency_id': order.pricelist_id.currency_id.id,
                'journal_id': len(journal_ids) and journal_ids[0] or False,
                'invoice_line': [(6, 0, inv_lines)],
                'origin': order.name,
                'fiscal_position': order.fiscal_position.id or False,
                'payment_term': order.payment_term_id.id or False,
                'company_id': order.company_id.id,
                'package_and_forwording':pkg_frwrd or 0.0,
                'insurance':insurance or 0.0,
                'freight':freight or 0.0,
            }
            inv_id = inv_obj.create(cr, uid, inv_data, context=context)

            # compute the invoice
            inv_obj.button_compute(cr, uid, [inv_id], context=context, set_total=True)

            # Link this new invoice to related purchase order
            order.write({'invoice_ids': [(4, inv_id)]}, context=context)
            res = inv_id
        return res

purchase_order()

class purchase_order_line(osv.osv):
    _inherit = 'purchase.order.line'

    def _get_p_uom_id(self, cr, uid, *args):
        cr.execute('select id from product_uom order by id limit 1')
        res = cr.fetchone()
        return res and res[0] or False

    def _amount_line(self, cr, uid, ids, prop, arg, context=None):
        """
        Process
            -Concept totally changed
                Purchase Qty and Purchase Rate should be comes into the picture instead of base qty and base rate
        """
        res = {}
        cur_obj=self.pool.get('res.currency')
        tax_obj = self.pool.get('account.tax')
        for line in self.browse(cr, uid, ids, context=context):
            res[line.id] = {
                'price_subtotal': 0.0,
                'base_price_subtotal': 0.0,
            }

            #Calculate purchase value
            price = line.price_unit * (1 - (line.discount or 0.0) / 100.0)
            taxes = tax_obj.compute_all(cr, uid, line.taxes_id, price, line.product_qty, line.product_id, line.order_id.partner_id)
            cur = line.order_id.pricelist_id.currency_id
            res[line.id]['base_price_subtotal'] = cur_obj.round(cr, uid, cur, taxes['total'])

            #Calculate base value
            base_price = line.purchase_unit_rate * (1 - (line.discount or 0.0) / 100.0)
            taxes = tax_obj.compute_all(cr, uid, line.taxes_id, base_price, line.line_qty, line.product_id, line.order_id.partner_id)
            res[line.id]['price_subtotal'] = cur_obj.round(cr, uid, cur, taxes['total'])

        return res

    _columns = {
        'product_qty': fields.float('Required Qty', digits_compute=dp.get_precision('Product Unit of Measure'), required=True),
        'line_qty': fields.float('Purchase Qty'),
        'line_uom_id':  fields.many2one('product.uom', 'Purchase UoM'),
        'uom_char': fields.char('char',size=250),
        'purchase_unit_rate': fields.float('Purchase Rate'),
        'consignment_variation': fields.char('Variation(±)'),
        'process_move_id':fields.many2one('stock.moves.workorder', 'Process Line'),
        'symbol': fields.related('order_id', 'currency_id','symbol', type="char",string="in",readonly=True),
        'date_planned': fields.date('Required Date', required=True),#just overwrited for string
        'price_subtotal': fields.function(_amount_line, multi="subt",string='Subtotal', digits_compute= dp.get_precision('Account')),
        'base_price_subtotal': fields.function(_amount_line, multi="subt", string='Base Subtotal', digits_compute= dp.get_precision('Account')),
    }

    _defaults = {
        'line_uom_id':_get_p_uom_id,
        'consignment_variation':'0.0',
        
    }

    def onchange_line_uom_id(self, cr, uid, ids, purchase_uom_id, context=None):
        """
        Attached Purchase Uom with Purchase rate like,
            Purchase rate : 10.00 /kg
                            25.00 /mtr
        """
        uom_obj = self.pool.get('product.uom')
        res = {'value': {'uom_char': ''}}
        if purchase_uom_id:
            name = uom_obj.browse(cr, uid, purchase_uom_id).name
            res['value'].update({'uom_char': name and '/'+ str(name) or ''})
        return res

    def onchange_product_id(self, cr, uid, ids, pricelist_id, product_id, qty, uom_id,
            partner_id, date_order=False, fiscal_position_id=False, date_planned=False,
            name=False, price_unit=False, context=None):
        """
        onchange handler of product_id.
        Process:
            Onchange should not be fire when in Amendment Process, so i just stop it.
        """
        prod_obj = self.pool.get('product.product')
        #Stop to fire onchange in Amendment Process
        for go in self.browse(cr, uid, ids):
            if go.order_id and go.order_id.is_amendment: return {}

        res = super(purchase_order_line, self).onchange_product_id(cr, uid, ids, pricelist_id, product_id, qty, uom_id,
            partner_id, date_order=date_order, fiscal_position_id=fiscal_position_id, date_planned=date_planned,
            name=name, price_unit=price_unit, context=context)
        if product_id:
            p = prod_obj.browse(cr, uid, product_id)
            p_qty = res['value'].get('product_qty', 0.0)
            res['value'].update({
                        'line_qty': p_qty * p.p_coefficient,
                        'line_uom_id':p.p_uom_id.id,
                        'purchase_unit_rate':p.purchase_price 
                        })
        return res

    def create(self, cr, uid, vals , context=None):
        """
            Process
                -Future use
        """
        return super(purchase_order_line, self).create(cr, uid, vals, context=context)


    def add_variations(self, cr, uid, ids , context=None):
        """
            Process
                -call wizard to add variation on line
        """
        context = context or {}
        models_data = self.pool.get('ir.model.data')
        # Get consume wizard
        dummy, form_view = models_data.get_object_reference(cr, uid, 'l10n_in_mrp_subcontract', 'view_consignment_variation_po')
        current = self.browse(cr, uid, ids[0], context=context)
        context.update({
                        'uom': current.line_uom_id.name,
                        })
        return {
            'name': _('Add Consignment Variation'),
            'view_type': 'form',
            'view_mode': 'form',
            'context':context,
            'res_model': 'consignment.variation.po',
            'views': [(form_view or False, 'form')],
            'type': 'ir.actions.act_window',
            'target':'new'
        }


purchase_order_line()

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
