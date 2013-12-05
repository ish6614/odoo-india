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

from openerp.osv import fields, osv
from openerp import netsvc
from openerp.tools.translate import _
import openerp.addons.decimal_precision as dp

class dispatch_mode(osv.Model):
    _name = 'dispatch.mode'

    _columns = {
        'name': fields.char('Name', size=256, required=True),
        'code': fields.char('Code', size=256, required=True),
    }

dispatch_mode()

class stock_picking(osv.Model):
    _inherit = "stock.picking"
    _table = "stock_picking"
    _order = "name desc"

    _columns = {
        'indent_id': fields.related('move_lines', 'indent_id', type='many2one', relation='indent.indent', string='Indents'),
        'indent_date': fields.related('indent_id', 'indent_date', type='datetime', relation='indent.indent', string='Indent Date', readonly=True),
        'indentor_id': fields.related('move_lines', 'indentor_id', type='many2one', relation='res.users', string='Indentor'),
        'department_id': fields.related('move_lines', 'department_id', type='many2one', relation='stock.location', string='Department'),
        'product_id': fields.related('move_lines', 'product_id', type='many2one', relation='product.product', string='Products'),
        'type': fields.selection([('out', 'Sending Goods'),('opening', 'Opening'), ('receipt', 'Receipt'), ('in', 'Getting Goods'), ('internal', 'Internal')], 'Shipping Type', required=True, select=True, help="Shipping type specify, goods coming in or going out."),
        'warehouse_id': fields.related('purchase_id', 'warehouse_id', type='many2one', relation='stock.warehouse', string='Destination Warehouse'),
        'state': fields.selection([
                ('draft', 'Draft'),
                ('cancel', 'Cancelled'),
                ('auto', 'Waiting Another Operation'),
                ('confirmed', 'Waiting Availability'),
                ('assigned', 'Available'),
                ('done', 'Transferred'),
            ], 'Status', readonly=True, select=True, track_visibility='onchange'
        ),
        'date_done': fields.datetime('Date of Transfer', help="Date of Completion", track_visibility='onchange'),
        'move_lines': fields.one2many('stock.move', 'picking_id', 'Internal Moves'),
        'purchase_id': fields.many2one('purchase.order', 'Purchase Order', ondelete='set null', select=True),

        # Fields for inward
        'challan_no': fields.char("Challan #", size=256),
        'inward_type':fields.selection([('noncash','Non-Cash Inward'), ('cash','Cash Inward'), ('foc', 'Free Of Cost')], 'Inward Type', track_visibility='onchange'),
        'transporter':fields.many2one('res.partner', 'Transporter'),
        'despatch_mode': fields.many2one('dispatch.mode', 'Despatch Mode'),

        # Fields for receipt
        'freight':fields.float('Freight', track_visibility='onchange'),
        'new_rate': fields.float('Rate', help="Rate for the product which is calculate after adding all tax"),
        'excies': fields.float('Excies'),
        'cess': fields.float('Cess'),
        'higher_cess': fields.float('Higher Cess'),
        'import_duty': fields.float('Import Duty'),
        'exe_excies': fields.float('Exempted Excies'),
        'exe_cess': fields.float('Exempted Cess'),
        'exe_higher_cess': fields.float('Exempted Higher Cess'),
        'exe_import_duty': fields.float('Import Duty'),
        'total_cost': fields.float('Total', digits_compute=dp.get_precision('Account')),
        'packing_unit': fields.float('Packing Unit', digits_compute=dp.get_precision('Account')),
        'insurance_unit': fields.float('Insurance Unit', digits_compute=dp.get_precision('Account')),
        'freight_unit': fields.float('Freight Unit', digits_compute=dp.get_precision('Account')),
        'analytic_account_id':fields.many2one('account.analytic.account','Project'),
        'discount': fields.float('Discount'),
    }

    _defaults = {
        'date_done':time.strftime('%Y-%m-%d %H:%M:%S'),
        'inward_type':'noncash',
    }

    def action_cancel_draft(self, cr, uid, ids, context=None):
        self.write(cr, uid, ids, {'state':'draft'}, context=context)
        for pick in self.pool.get('stock.picking').browse(cr, uid, ids, context=context):
            for move in pick.move_lines:
                self.pool.get('stock.move').write(cr, uid, [move.id], {'state': 'draft'})
        wf_service = netsvc.LocalService("workflow")
        for picking_id in ids:
            wf_service.trg_delete(uid, 'stock.picking', picking_id, cr)
            wf_service.trg_create(uid, 'stock.picking', picking_id, cr)
        return True

    def _get_discount_invoice(self, cr, uid, move_line):
        return move_line.discount

    def _prepare_invoice(self, cr, uid, picking, partner, inv_type, journal_id, context=None):
        res = super(stock_picking, self)._prepare_invoice(cr, uid, picking=picking, partner=partner, inv_type=inv_type, journal_id=journal_id, context=context)
        freight = insurance = package_and_forwording = vat_amount = advance_amount = retention_amount = 0.0
        purchase_id =  picking.purchase_id.id,
        picking_in_id = self.search(cr, uid, [('type','=','in'), ('purchase_id','=',picking.purchase_id.id)], context=context)[0],
        picking_receipt_id = picking.type == 'receipt' and picking.id

        for move in picking.move_lines:
            freight += move.freight_unit * move.product_qty
            insurance += move.insurance_unit * move.product_qty
            package_and_forwording += move.packing_unit * move.product_qty
            vat_amount += move.vat_unit * move.product_qty
            retention_amount += move.retention_unit * move.product_qty
            advance_amount += move.advance_adjustment
        res = dict(res, freight=freight, insurance=insurance, package_and_forwording=package_and_forwording, advance_amount=advance_amount,vat_amount=vat_amount, purchase_id=purchase_id, picking_in_id=picking_in_id, picking_receipt_id=picking_receipt_id, lab_no=picking.lab_no, retention_amount=retention_amount)
        return res

    def action_invoice_create(self, cr, uid, ids, journal_id=False, group=False, type='out_invoice', context=None):
        type = 'in_invoice'
        res = super(stock_picking, self).action_invoice_create(cr, uid, ids, journal_id=journal_id, group=group, type=type, context=context)
        return res

    def do_partial(self, cr, uid, ids, partial_datas, context=None):
        receipt_obj = self.pool.get('stock.picking.receipt')
        stock_move = self.pool.get('stock.move')
        warehouse_obj = self.pool.get('stock.warehouse')
        track_obj = self.pool.get('stock.move.tracking')

        for pick in self.browse(cr, uid, ids, context=context):
            total_challan_qty = 0.0
            for move in pick.move_lines:
                qty_variance = move.product_qty * (1+ (move.product_id.variance)/ 100)
                if move.product_qty <= move.challan_qty and move.challan_qty > qty_variance:
                    raise osv.except_osv(_('Error!'), _('Unable to process document as challan quantity  can not be more then ordered quantity including variance !'))

                total_challan_qty += move.challan_qty
        if total_challan_qty <= 0:
            raise osv.except_osv(_('Error!'), _('Unable to process document as all challan quantity are 0 in this document !'))

        #Does entry got the issued item with affect for the new rate and decrease qty
        if context.get('active_model') == 'stock.picking':
            for pick in self.browse(cr, uid, ids, context=context):
                if pick.reverse_transfer:
                    track_obj.create_stock_move_tracking(cr, uid, pick, context=context)
                else:
                    self.update_issue_rate(cr, uid, [pick.id], context)
                    self.issue_items(cr, uid, [pick.id], context)

        res = super(stock_picking, self).do_partial(cr, uid, ids, partial_datas, context=context)
        for pick in res:
            self.write(cr, uid, [pick], {'name':False})

        vals = {}
        if context.get('default_type') == 'in':
            move_line = []
            for pick in self.browse(cr, uid, ids, context=context):
                if pick.inward_type == 'foc':
                    continue

                warehouse_dict = False
                if pick.purchase_id:
                    warehouse_dict = warehouse_obj.read(cr, uid, pick.warehouse_id.id, ['lot_input_id', 'lot_stock_id'], context=context)
                else:
                    warehouse_ids = warehouse_obj.search(cr, uid, [('company_id','=',pick.company_id.id)])
                    if warehouse_ids:
                        warehouse_dict = warehouse_obj.read(cr, uid, warehouse_ids[0], ['lot_input_id', 'lot_stock_id'], context=context)
                    else:
                        raise osv.except_osv(_('Configuration Error!'), _('Unable to locate warehouse from Order or Company !'))

                if pick.state == 'done':
                    for move in pick.move_lines:
                        dict = stock_move.onchange_amount(cr, uid, [move.id], pick.purchase_id.id, move.product_id.id, 0, 0, 0, 0, 0, context)
                        dict['value'].update({'location_id': warehouse_dict.get('lot_input_id', False)[0], 'location_dest_id': warehouse_dict.get('lot_stock_id', False)[0]})
                        move_line.append(stock_move.copy(cr, uid, move.id, dict['value'], context=context))
                        if move.product_id and move.product_id.ex_chapter:
                            vals.update({'excisable_item': True})
                    vals.update({'inward_id': pick.id or False})
                else:
                    for move in pick.backorder_id.move_lines:
                        dict = stock_move.onchange_amount(cr, uid, [move.id], pick.purchase_id.id, move.product_id.id, 0, 0, 0, 0, 0, context)
                        dict['value'].update({'location_id': warehouse_dict.get('lot_input_id', False)[0], 'location_dest_id': warehouse_dict.get('lot_stock_id', False)[0]})
                        move_line.append(stock_move.copy(cr, uid, move.id, dict['value'], context=context))
                        for move in pick.move_lines:
                            stock_move.write(cr, uid, [move.id], {'challan_qty':0.0})
                        if move.product_id and move.product_id.ex_chapter:
                            vals.update({'excisable_item': True})
                    vals.update({'inward_id': pick.backorder_id and pick.backorder_id.id or False})

                vals.update({'name': False,
                    'partner_id': pick.partner_id.id,
                    'stock_journal_id': pick.stock_journal_id.id or False,
                    'origin': pick.origin or False,
                    'type': 'receipt',
                    'purchase_id': pick.purchase_id.id or False,
                    'move_lines': [(6, 0, move_line)],
                    'challan_no':pick.challan_no,
                    'date_done':pick.date_done,
                    'lab_no': pick.lab_no,
                })
            receipt_obj.create(cr, uid, vals, context=context)

        elif context.get('default_type') == 'receipt':
            for pick in self.browse(cr, uid, ids, context=context):
                move_obj = self.pool.get('stock.move')
                purchase_obj = self.pool.get('purchase.order')
                purchase_line_obj = self.pool.get('purchase.order.line')
                if pick.state == 'done':
                    advance_adjustment = pick.purchase_id.advance_adjustment
                    total_ajustment_amt = pick.purchase_id.total_ajustment_amt
                    for move in pick.move_lines:
                        amount = move.challan_qty * move.rate
                        advance_amt = pick.purchase_id.advance_amount - advance_adjustment
                        total_amt = pick.purchase_id.amount_untaxed - total_ajustment_amt
                        advance_adjust = (amount * advance_amt) / total_amt
                        advance_adjustment += advance_adjust
                        total_ajustment_amt += amount
                        purchase_line_obj.write(cr, uid, move.purchase_line_id.id, {'advance_adjust': move.purchase_line_id and move.purchase_line_id.advance_adjust or 0.0 + advance_adjust},context)
                        move_obj.write(cr, uid, move.id, {'advance_adjustment': advance_adjust},context)
                    purchase_obj.write(cr, uid, pick.purchase_id.id, {'advance_adjustment': advance_adjustment, 'total_ajustment_amt' : total_ajustment_amt},context)
        return res

stock_picking()

class stock_picking_in(osv.osv):
    _inherit = "stock.picking.in"
    _table = "stock_picking"
    _order = "name desc"

    _columns = {
        'indent_id': fields.related('move_lines', 'indent_id', type='many2one', relation='indent.indent', string='Indents'),
        'indent_date': fields.related('indent_id', 'indent_date', type='datetime', relation='indent.indent', string='Indent Date', store=True, readonly=True),
        'indentor_id': fields.related('move_lines', 'indentor_id', type='many2one', relation='res.users', string='Indentor'),
        'department_id': fields.related('move_lines', 'department_id', type='many2one', relation='stock.location', string='Department'),
        'product_id': fields.related('move_lines', 'product_id', type='many2one', relation='product.product', string='Products'),
        'purchase_id': fields.many2one('purchase.order', 'Purchase Order', ondelete='set null', select=True),

        'challan_no': fields.char("Challan #", size=256),
        'inward_type':fields.selection([('noncash','Non-Cash Inward'), ('cash','Cash Inward'), ('foc', 'Free Of Cost')], 'Inward Type', track_visibility='onchange'),
        'transporter':fields.many2one('res.partner', 'Transporter'),
        'despatch_mode': fields.many2one('dispatch.mode', 'Despatch Mode'),
        'state': fields.selection([
                ('draft', 'Draft'),
                ('cancel', 'Cancelled'),
                ('auto', 'Waiting Another Operation'),
                ('confirmed', 'Waiting Availability'),
                ('assigned', 'Available'),
                ('done', 'Transferred'),
            ], 'Status', readonly=True, select=True, track_visibility='onchange'
        ),
         'date_done': fields.datetime('Date of Transfer', help="Date of Completion", track_visibility='onchange'),
    }

    _defaults = {
        'inward_type':'noncash',
    }

    def action_cancel_draft(self, cr, uid, ids, context=None):
        self.write(cr, uid, ids, {'state':'draft'}, context=context)
        for pick in self.pool.get('stock.picking').browse(cr, uid, ids, context=context):
            for move in pick.move_lines:
                self.pool.get('stock.move').write(cr, uid, [move.id], {'state': 'draft'}, context=context)
        wf_service = netsvc.LocalService("workflow")
        for picking_id in ids:
            wf_service.trg_delete(uid, 'stock.picking', picking_id, cr)
            wf_service.trg_create(uid, 'stock.picking', picking_id, cr)
        return True

    def receipt_tree_view(self, cr, uid, ids, context):
        mod_obj = self.pool.get('ir.model.data')
        action_model, action_id = tuple(mod_obj.get_object_reference(cr, uid, 'maize_purchase', 'action_picking_tree4_receipt'))
        action = self.pool.get(action_model).read(cr, uid, action_id, context=context)
        action_ctx = str(action['context']).strip()
        ctx = eval(action_ctx)
        ctx.update({
            'search_default_inward_id': ids,
        })
        action.update({'context': ctx, })
        return action

stock_picking_in()

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4: