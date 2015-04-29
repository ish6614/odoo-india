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

from openerp.osv import fields, osv
import openerp.addons.decimal_precision as dp
from openerp.tools.amount_to_text_en import amount_to_text

class account_invoice_line(osv.osv):
    _inherit = "account.invoice.line"

    def _amount_line(self, cr, uid, ids, prop, unknow_none, unknow_dict):
        """
        -Process
            -added variation amount in line
        """
        res = {}
        tax_obj = self.pool.get('account.tax')
        cur_obj = self.pool.get('res.currency')
        for line in self.browse(cr, uid, ids):
            price = line.price_unit  * (1-(line.discount or 0.0)/100.0)
            taxes = tax_obj.compute_all(cr, uid, line.invoice_line_tax_id, price, line.quantity, product=line.product_id, partner=line.invoice_id.partner_id)
            res[line.id] = taxes['total'] + line.variation_amount
            if line.invoice_id:
                cur = line.invoice_id.currency_id
                res[line.id] = cur_obj.round(cr, uid, cur, res[line.id])
        return res

    _columns = {
        'pur_line_qty': fields.float('Required Quantity'),
        'pur_line_uom_id':  fields.many2one('product.uom', 'Base UoM'),
        'variation_amount': fields.float('Variation Amount(±)', digits_compute=dp.get_precision('Account')),
        'price_subtotal': fields.function(_amount_line, string='Amount', type="float",
            digits_compute= dp.get_precision('Account'), store=True),
    }

account_invoice_line()

class account_invoice(osv.osv):
    _inherit = 'account.invoice'

    def _amount_all(self, cr, uid, ids, name, args, context=None):
        res = {}
        for invoice in self.browse(cr, uid, ids, context=context):
            res[invoice.id] = {
                'amount_untaxed': 0.0,
                'amount_tax': 0.0,
                'amount_total': 0.0
            }
            for line in invoice.invoice_line:
                res[invoice.id]['amount_untaxed'] += line.price_subtotal
            for line in invoice.tax_line:
                res[invoice.id]['amount_tax'] += line.amount
            other_charges = (invoice.package_and_forwording + invoice.freight + invoice.insurance + invoice.extra_charges + invoice.round_off)
            res[invoice.id]['amount_total'] = res[invoice.id]['amount_tax'] + res[invoice.id]['amount_untaxed'] + other_charges
        return res

    def _get_invoice_line(self, cr, uid, ids, context=None):
        result = {}
        for line in self.pool.get('account.invoice.line').browse(cr, uid, ids, context=context):
            result[line.invoice_id.id] = True
        return result.keys()

    def _get_invoice_tax(self, cr, uid, ids, context=None):
        result = {}
        for tax in self.pool.get('account.invoice.tax').browse(cr, uid, ids, context=context):
            result[tax.invoice_id.id] = True
        return result.keys()

    _columns = {
        'do_id': fields.many2one('stock.picking', 'Delivery Order', readonly=True),
        'do_address_id': fields.many2one('res.partner', 'Delivery Address'),
        'so_date': fields.date('Sales Date'),
        #'do_carrier_id': fields.many2one('delivery.carrier', 'Carrier'),
        'do_name': fields.char('Delivery Name'),
        'do_delivery_date': fields.datetime('Delivery Date'),
        'so_id': fields.many2one('sale.order', 'Sale Order ID', readonly=True),
        'desc_of_pkg': fields.char('Description of Package', size=256),
        'total_pkg': fields.char('Package and Qty', size=50),
        'tarrif_no': fields.integer('Tarrif No.'),
        'total_net_weight':  fields.char('Total Net Weight', size=50),

        'package_and_forwording': fields.float('Packaging & Forwarding', states={'confirmed':[('readonly', True)], 'approved':[('readonly', True)], 'done':[('readonly', True)]}),
        'insurance': fields.float('Insurance', states={'confirmed':[('readonly', True)], 'approved':[('readonly', True)], 'done':[('readonly', True)]}),
        'freight': fields.float('Freight', states={'confirmed':[('readonly', True)], 'approved':[('readonly', True)], 'done':[('readonly', True)]}),
        'extra_charges': fields.float('Other Charges', states={'confirmed':[('readonly', True)], 'approved':[('readonly', True)], 'done':[('readonly', True)]}),
        'round_off': fields.float('Round Off', states={'confirmed':[('readonly', True)], 'approved':[('readonly', True)], 'done':[('readonly', True)]}, help="Round Off Amount"),

        'amount_untaxed': fields.function(_amount_all, digits_compute=dp.get_precision('Account'), string='Subtotal', track_visibility='always',
            store={
                'account.invoice': (lambda self, cr, uid, ids, c={}: ids, ['package_and_forwording','insurance', 'freight' 'extra_charges','round_off','invoice_line'], 20),
                'account.invoice.tax': (_get_invoice_tax, None, 20),
                'account.invoice.line': (_get_invoice_line, ['price_unit', 'invoice_line_tax_id', 'quantity', 'discount', 'invoice_id', 'packaging_cost'], 20),
            },
            multi='all'),
        'amount_tax': fields.function(_amount_all, digits_compute=dp.get_precision('Account'), string='Tax',
            store={
                'account.invoice': (lambda self, cr, uid, ids, c={}: ids, ['package_and_forwording','insurance', 'freight' 'extra_charges','round_off','invoice_line'], 20),
                'account.invoice.tax': (_get_invoice_tax, None, 20),
                'account.invoice.line': (_get_invoice_line, ['price_unit', 'invoice_line_tax_id', 'quantity', 'discount', 'invoice_id', 'packaging_cost'], 20),
            },
            multi='all'),
        'amount_total': fields.function(_amount_all, digits_compute=dp.get_precision('Account'), string='Total',
            store={
                'account.invoice': (lambda self, cr, uid, ids, c={}: ids, ['package_and_forwording','insurance', 'freight' 'extra_charges','round_off','invoice_line'], 20),
                'account.invoice.tax': (_get_invoice_tax, None, 20),
                'account.invoice.line': (_get_invoice_line, ['price_unit', 'invoice_line_tax_id', 'quantity', 'discount', 'invoice_id', 'packaging_cost'], 20),
            },
            multi='all'),
            }

    def copy(self, cr, uid, id, default=None, context=None):
        default = default or {}
        default.update({
            'do_id':False,
            'do_address_id': False,
            #'do_carrier_id': False,
            'so_date': False,
            'do_name': False,
            'do_delivery_date': False,
            'so_id': False,
            'desc_of_pkg': False,
            'total_pkg': False,
            'total_net_weight':False
        })
        return super(account_invoice, self).copy(cr, uid, id, default, context)

    def _get_qty_total(self, cr, uid, ids):
        res = {}
        qty = 0.0
        for invoice in self.browse(cr, uid, ids):
            for line in invoice.invoice_line:
                qty += line.quantity
            res[invoice.id] = qty
        return res

    def amount_to_text(self, amount):
        amount_in_word = amount_to_text(amount)
        amount_in_word = amount_in_word.replace("euro", "Rupees").replace("Cents", "Paise").replace("Cent", "Paise")
        return amount_in_word

account_invoice()

# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
