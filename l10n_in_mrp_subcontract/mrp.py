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
from openerp import netsvc
import time
import math

from openerp.osv import osv, fields
import openerp.addons.decimal_precision as dp
from openerp.tools.translate import _
from openerp.tools import float_compare
from openerp.tools import DEFAULT_SERVER_DATETIME_FORMAT
from openerp import tools
from datetime import datetime

STATE_SELECTION = [
        ('draft', 'Draft'),
        ('in_progress', 'In Progress'),
        ('consumed', 'Consumed'),
        ('finished', 'Finished'),
        ('pending', 'Pending'),
        ('cancelled', 'Cancelled'),
        ('rejected', 'Rejected')
    ]

def rounding(f, r):
    import math
    if not r:
        return f
    return math.ceil(f / r) * r

class mrp_production(osv.osv):
    _inherit = 'mrp.production'
    _order = "id desc"

    def _produced_qty_calc(self, cr, uid, ids, name, args, context=None):
        """
        Process
            -Find already Produce Qty from Manufacturing Order.
        """
        result = dict([(id, {'already_produced_qty': 0.0}) for id in ids])
        for prod in self.browse(cr, uid, ids, context=context):
            done = -(prod.scraped_qty)
            for move in prod.move_created_ids2:
                if move.product_id == prod.product_id:
                    #ignore scrapped and extra consumed and cancel moves
                    if move.state <> 'cancel':
                        if (not move.scrapped) or (not move.extra_consumed):
                            done += move.product_qty
            result[prod.id]['already_produced_qty'] = done
        return result

    def _mrp_costing(self, cr, uid, ids, name, args, context=None):
        """
        Process
            -Planned Cost = cost hour * planned time
            -Actual Cost = cost hour * actual time
        """
        result = dict([(id, {'planned_cost': 0.0, 'actual_cost': 0.0}) for id in ids])
        for order in self.browse(cr, uid, ids, context=context):
            planned_cost = actual_cost = 0.0
            for wo in order.workcenter_lines:
                if wo.state == 'cancel': continue
                planned_cost += wo.hour * wo.workcenter_id.costs_hour
                actual_cost += wo.delay * wo.workcenter_id.costs_hour
            result[order.id]['planned_cost'] = planned_cost
            result[order.id]['actual_cost'] = actual_cost
        return result

    _columns = {
        'workcenter_lines': fields.one2many('mrp.production.workcenter.line', 'production_id', 'Work Centers Utilisation',
            readonly=False, states={'done':[('readonly', True)]}),
        'moves_to_workorder': fields.boolean('Materials Moves To Work-Center?'),
        'customer_id': fields.many2one('res.partner', 'Customer', readonly=True, states={'draft':[('readonly',False)]}),
        'sale_order_id': fields.many2one('sale.order', 'Sale Order', readonly=True),
        'procurement_generated': fields.boolean('Procurement Generated?'),
        'parent_id': fields.many2one('mrp.production', 'Parent Order', readonly=True),
        'scrap_order_id': fields.many2one('stock.picking', 'Scrap Order', readonly=True),
        'already_produced_qty': fields.function(_produced_qty_calc, multi='produced', type='float', string='Produced Qty',digits_compute=dp.get_precision('Product Unit of Measure')),
        'scraped_qty': fields.float('Scraped Quantity', digits_compute=dp.get_precision('Product Unit of Measure')),
        'backorder_ids': fields.one2many('mrp.production', 'parent_id','Split Orders', readonly=True),
        'date_planned': fields.datetime('Scheduled Date', required=True, select=1, readonly=False, states={'done':[('readonly',True)]}),
        'state': fields.selection(
            [('split_order','Split Order'),('draft', 'New'), ('cancel', 'Cancelled'), ('picking_except', 'Picking Exception'), ('confirmed', 'Awaiting Raw Materials'),
                ('ready', 'Ready to Produce'), ('in_production', 'Production Started'), ('done', 'Done')],
            string='Status', readonly=True,
            track_visibility='onchange',
            help="When the production order is created the status is set to 'Draft'.\n\
                If the order in split mode the status is set to 'Split Order'.\n\
                If the order is confirmed the status is set to 'Waiting Goods'.\n\
                If any exceptions are there, the status is set to 'Picking Exception'.\n\
                If the stock is available then the status is set to 'Ready to Produce'.\n\
                When the production gets started then the status is set to 'In Production'.\n\
                When the production is over, the status is set to 'Done'."),

        'currency_id': fields.related('company_id', 'currency_id', type="many2one", relation="res.currency", string="Currency", readonly=True),
        'planned_cost': fields.function(_mrp_costing, multi='cost', type='float', string='Planned Cost'),
        'actual_cost': fields.function(_mrp_costing, multi='cost', type='float', string='Actual Cost'),

    }

    def split_qty_order(self, cr, uid, ids, context=None):
        """
        -Process
            Split production order with partially quantity.
        """
        context = context or {}
        prod = self.browse(cr, uid, ids[0], context)
        context.update({'product_id':prod.product_id.id, 'qty': prod.product_qty})
        return {
                'name': 'Split Order Quantity',
                'view_mode': 'form',
                'view_type': 'form',
                'res_model': 'split.production.order.qty',
                'type': 'ir.actions.act_window',
                'context':context,
                'target':'new'
                }

    def set_to_draft_order(self, cr, uid, ids, context=None):
        """
        -Process
            Set to order in draft state
        """
        return self.write(cr, uid, ids, {'state':'draft'})

    def open_procurements(self, cr, uid, ids, context=None):
        """
        -Process
            -Open Procurments, which is waiting for what .. ??
            -User easily handles procurments from manufacturing order.
        """
        context = context or {}
        procurment_obj = self.pool.get('procurement.order')
        models_data = self.pool.get('ir.model.data')
        orderp_obj = self.pool.get('stock.warehouse.orderpoint')
        data= self.browse(cr, uid, ids[0])
        if not data.procurement_generated:
            procurment_obj._procure_orderpoint_confirm(cr, uid, context=context)

        raw_material_ids = list(set([x.product_id.id for x in data.move_lines]))
#        search_args = (data.origin or '')+':'+(data.name or '')
#        filter_rm_ids = orderp_obj.search(cr, uid, [('product_id', 'in' ,raw_material_ids)])
#        for op_data in orderp_obj.browse(cr, uid, filter_rm_ids):
#            search_args += '|'+op_data.name
#        if search_args:
#            search_args += '%('+search_args+')%'
#        print "search_args",search_args
#        cr.execute("""
#                        SELECT id FROM procurement_order 
#                        WHERE origin SIMILAR TO %s
#                        AND state not in ('done','cancel')
#                        AND product_id in %s
#                    """,
#            [search_args, tuple(raw_material_ids)])
        procurments_ids = []
        if raw_material_ids:
            cr.execute("""
                            SELECT id FROM procurement_order
                            WHERE 
                            state not in ('done','cancel')
                            AND product_id in %s
                        """,
                [tuple(raw_material_ids)])
            procurments_ids = [x[0] for x in cr.fetchall()]
        #procurments_ids = procurment_obj.search(cr, uid, [('origin','ilike',':'+data.name)], context=context)
        # Get opportunity views
        dummy, form_view = models_data.get_object_reference(cr, uid, 'procurement', 'procurement_form_view')
        dummy, tree_view = models_data.get_object_reference(cr, uid, 'procurement', 'procurement_tree_view')
        context.update({'active_model': 'procurement.order', 'active_ids': procurments_ids})
        data.write({'procurement_generated':True})
        return {
                'domain': "[('id','in',["+','.join(map(str, procurments_ids))+"])]",
                'name': 'Procurements Order',
                'view_mode': 'form',
                'view_type': 'form',
                'res_model': 'procurement.order',
                'type': 'ir.actions.act_window',
                'view_id': False,
                'views': [(tree_view or False, 'tree'),
                          (form_view or False, 'form'),
                        ],
                }

    def action_cancel(self, cr, uid, ids, context=None):
        """ Cancels the production order and related stock moves.
        @return: True
        -Process
            -Cancel all process lines in workorder
        """
        for data in self.browse(cr, uid, ids, context=context):
            for wrkorder in data.workcenter_lines:
                for process_mv in wrkorder.moves_workorder:
                    if process_mv.state == 'in_progress':
                        raise osv.except_osv(_('Can"t cancel Production order!'), _('Process line has been started in workorder(%s)'%(wrkorder.name)))
        return super(mrp_production, self).action_cancel(cr, uid, ids, context=context)

    def product_moves_to_workcenter(self, cr, uid, ids , context=None):
        """
        -Process
            -getting all moves to consume
            -pass that moves to _update_workorder_lines() for create workorder process lines
            -Update moves_to_workorder=True
        """

        def _none_qty(production_rec):
            """
            -Process
                getting total moves to consume
            """
            total_moves_to_consume = []
            for raw_m in production_rec.move_lines:
                total_moves_to_consume.append(raw_m.id)
            if not total_moves_to_consume:
                raise osv.except_osv(_('Raw material not found!'), _('Raw material not found for consume'))
            return total_moves_to_consume

        move_obj = self.pool.get('stock.move')
        production_rec = self.browse(cr, uid, ids[0], context=context)
        if production_rec.state <> 'ready':
            raise osv.except_osv(_('Production order not ready for start!'), _('You only put raw material into production department if production order must be into "ready to produce" state.'))
        if production_rec.moves_to_workorder:
            raise osv.except_osv(_('Warning!'), _('Raw materials already moved to workorder.'))

        total_moves_to_consume = _none_qty(production_rec)
        self._update_workorder_lines(cr, uid, total_moves_to_consume, ids[0], context=context)
        production_rec.write({'moves_to_workorder':True})
        move_obj.write(cr, uid, total_moves_to_consume, {'moves_to_workorder':True}, context=context)
        return True

    def action_produce(self, cr, uid, production_id, production_qty, production_mode, context=None):
        """ To produce final product based on production mode (consume/consume&produce).
        If Production mode is consume, all stock move lines of raw materials will be done/consumed.
        If Production mode is consume & produce, all stock move lines of raw materials will be done/consumed
        and stock move lines of final product will be also done/produced.
        @param production_id: the ID of mrp.production object
        @param production_qty: specify qty to produce
        @param production_mode: specify production mode (consume/consume&produce).
        @return: True

        * Our Goal
        - Process
            -We are here totally depended on workorder thatwhy just commented raise warning for cosuming raw materials
        """
        stock_mov_obj = self.pool.get('stock.move')
        production = self.browse(cr, uid, production_id, context=context)

        wf_service = netsvc.LocalService("workflow")
        if not production.move_lines and production.state == 'ready':
            # trigger workflow if not products to consume (eg: services)
            wf_service.trg_validate(uid, 'mrp.production', production_id, 'button_produce', cr)

        produced_qty = 0
        for produced_product in production.move_created_ids2:
            if (produced_product.scrapped) or (produced_product.product_id.id != production.product_id.id):
                continue
            produced_qty += produced_product.product_qty
        if production_mode in ['consume','consume_produce']:
            consumed_data = {}

            # Calculate already consumed qtys
            for consumed in production.move_lines2:
                #added dynamic raw material to compare with moves lines(Because of wan't breack any standard flow)
                if consumed.scrapped or consumed.extra_consumed:
                    continue
                if not consumed_data.get(consumed.product_id.id, False):
                    consumed_data[consumed.product_id.id] = 0
                consumed_data[consumed.product_id.id] += consumed.product_qty

            # Find product qty to be consumed and consume it
            for scheduled in production.product_lines:

                # total qty of consumed product we need after this consumption
                total_consume = ((production_qty + produced_qty) * scheduled.product_qty / production.product_qty)

                # qty available for consume and produce
                qty_avail = scheduled.product_qty - consumed_data.get(scheduled.product_id.id, 0.0)

                if qty_avail <= 0.0:
                    # there will be nothing to consume for this raw material
                    continue

                raw_product = [move for move in production.move_lines if move.product_id.id==scheduled.product_id.id]
                if raw_product:
                    # qtys we have to consume
                    qty = total_consume - consumed_data.get(scheduled.product_id.id, 0.0)
                    if float_compare(qty, qty_avail, precision_rounding=scheduled.product_id.uom_id.rounding) == 1:
                        # if qtys we have to consume is more than qtys available to consume
                        prod_name = scheduled.product_id.name_get()[0][1]
                        #HIDE THIS PROCESS ONLY , BECAUSE WE ARE TOTTALY DEPENDS ON WORKORDER NOT PRODUCED BUTTON.
                        #raise osv.except_osv(_('Warning!'), _('You are going to consume total %s quantities of "%s".\nBut you can only consume up to total %s quantities.') % (qty, prod_name, qty_avail))
                    if qty <= 0.0:
                        # we already have more qtys consumed than we need
                        continue

                    raw_product[0].action_consume(qty, raw_product[0].location_id.id, context=context)

        if production_mode == 'consume_produce':
            # To produce remaining qty of final product
            #vals = {'state':'confirmed'}
            #final_product_todo = [x.id for x in production.move_created_ids]
            #stock_mov_obj.write(cr, uid, final_product_todo, vals)
            #stock_mov_obj.action_confirm(cr, uid, final_product_todo, context)
            produced_products = {}
            for produced_product in production.move_created_ids2:
                if produced_product.scrapped:
                    continue
                if not produced_products.get(produced_product.product_id.id, False):
                    produced_products[produced_product.product_id.id] = 0
                produced_products[produced_product.product_id.id] += produced_product.product_qty

            for produce_product in production.move_created_ids:
                produced_qty = produced_products.get(produce_product.product_id.id, 0)
                subproduct_factor = self._get_subproduct_factor(cr, uid, production.id, produce_product.id, context=context)
                rest_qty = (subproduct_factor * production.product_qty) - produced_qty

                if rest_qty < (subproduct_factor * production_qty):
                    prod_name = produce_product.product_id.name_get()[0][1]
                    #HIDE THIS PROCESS ONLY , BECAUSE WE ARE TOTTALY DEPENDS ON WORKORDER NOT PRODUCED BUTTON.
                    #raise osv.except_osv(_('Warning!'), _('You are going to produce total %s quantities of "%s".\nBut you can only produce up to total %s quantities.') % ((subproduct_factor * production_qty), prod_name, rest_qty))
                if rest_qty > 0 :
                    stock_mov_obj.action_consume(cr, uid, [produce_product.id], (subproduct_factor * production_qty), context=context)

        for raw_product in production.move_lines2:
            new_parent_ids = []
            parent_move_ids = [x.id for x in raw_product.move_history_ids]
            for final_product in production.move_created_ids2:
                if final_product.id not in parent_move_ids:
                    new_parent_ids.append(final_product.id)
            for new_parent_id in new_parent_ids:
                stock_mov_obj.write(cr, uid, [raw_product.id], {'move_history_ids': [(4,new_parent_id)]})

        wf_service.trg_validate(uid, 'mrp.production', production_id, 'button_produce_done', cr)
        return True

    def _to_find_shortestworkorder(self, cr, uid, production_id):
        """
        - Process
            -get production_id,
            -find shortest sequence in workorder,
        - return
            -shortest workorder
        """
        short_key = {}
        production = self.browse(cr, uid, production_id)
        for wrkorder in production.workcenter_lines:
            # need to check for not included done or cancel work-order
            if wrkorder.state != 'cancel':
                short_key.update({wrkorder.id: wrkorder.sequence})

        # find shorted by values
        shorted_wrkordr = sorted(short_key.items(), key=lambda x: x[1])
        return shorted_wrkordr and shorted_wrkordr[0][0] or False

    def _create_process_dict(self, cr, uid, move, shortest_wrkorder):
        """
        - Process
            - pass moves data and shortest workorderid
        - Returns
            - Final dictionary of process move
        """
        # check for routing not found for production order
        # if not shortest_wrkorder:
        #    raise osv.except_osv(_('WorkCenter not found!'), _('WorkOrder not found to attach process flow,\nKindly attach atleast one route for production order'))
        if not shortest_wrkorder:
            return {}
        return {
                'name': move.name,
                'move_id': move.id,
                'workorder_id': shortest_wrkorder,
                'product_id': move.product_id.id,
                'uom_id': move.product_uom.id,
                'prodlot_id': move.prodlot_id and move.prodlot_id.id or False,
                'start_date':False,
                'end_date':False,
                'accepted_date':time.strftime(DEFAULT_SERVER_DATETIME_FORMAT),
                'total_qty': move.product_qty or 0.0,
                'accepted_qty': 0.0,
                'rejected_qty': 0.0,
                'reason': '',
                'state': 'draft',
               }

    def _update_workorder_lines(self, cr, uid, all_moves, production_id, context=None):
        """
        - Process:
            - find shorted workorder,
            - browse all consume moves and attached it workorder processing,
            - find shortest workorder to attached all lines to it,
        @return: dictionaries for moves to generate workorders(moves)
        """
        context = context or {}
        stock_move = self.pool.get('stock.move')
        process_move = self.pool.get('stock.moves.workorder')

        # process moves dictionaries, find shorted work-order by sequence.
        process_lines = []
        shortest_wrkorder = self._to_find_shortestworkorder(cr, uid, production_id)
        for c_moves in stock_move.browse(cr, uid, all_moves, context=context):
            process_lines.append(self._create_process_dict(cr, uid, c_moves, shortest_wrkorder))
        # filter to any None dictionary.
        process_lines = filter(None, process_lines)
        # create process moves for shorted work-order.
        for dicts in process_lines:
            process_move.create(cr, uid, dicts, context=context)
        return True

    def _check_for_routing(self, cr, uid, production, context=None):
        """
        Process
            -Find BoM from finish product,
                -First check from production order Routing
                -If not available then check from production BoM to Routing
        Return
            - Warning raise if routing not found at production order
        """
        routing_id = production.bom_id.routing_id.id or False
        if (not production.routing_id) and (not routing_id):
            raise osv.except_osv(_('Routing not found!'), _('Atleast define one route for starting of production order'))
        return True

    def test_if_product(self, cr, uid, ids):
        """
        Process
            -Check for BoM lines, If BoM lines not avail, It will generate warning message. 
        """
        for production in self.browse(cr, uid, ids):
            if production.bom_id and (not production.bom_id.bom_lines):
                raise osv.except_osv(_('BoM lines not found!'),_('Provide BoM lines for (%s)'%(production.bom_id.name)))
        return super(mrp_production, self).test_if_product(cr, uid, ids)

    def action_confirm(self, cr, uid, ids, context=None):
        """ 
        - Process
            - To check routing available to start production order,
            - To call _update_workorder_lines() at end of confirmation. 
        - Return
            - shipment_id(As it is)
        """
        # check for context
        context = context or {}
        shipment_id = False
        all_consume_moves = []
        wf_service = netsvc.LocalService("workflow")
        uncompute_ids = filter(lambda x:x, [not x.product_lines and x.id or False for x in self.browse(cr, uid, ids, context=context)])
        self.action_compute(cr, uid, uncompute_ids, context=context)
        for production in self.browse(cr, uid, ids, context=context):
            # check routing available or not
            self._check_for_routing(cr, uid, production, context=context)
            shipment_id = self._make_production_internal_shipment(cr, uid, production, context=context)
            produce_move_id = self._make_production_produce_line(cr, uid, production, context=context)

            source_location_id = production.location_src_id.id
            if production.bom_id.routing_id and production.bom_id.routing_id.location_id:
                source_location_id = production.bom_id.routing_id.location_id.id

            for line in production.product_lines:
                consume_move_id = self._make_production_consume_line(cr, uid, line, produce_move_id, source_location_id=source_location_id, context=context)
                # update all consume moves because its need to create process lines in work-order
                all_consume_moves.append(consume_move_id)
                if shipment_id:
                    shipment_move_id = self._make_production_internal_shipment_line(cr, uid, line, shipment_id, consume_move_id, \
                                 destination_location_id=source_location_id, context=context)
                    self._make_production_line_procurement(cr, uid, line, shipment_move_id, context=context)

            if shipment_id:
                wf_service.trg_validate(uid, 'stock.picking', shipment_id, 'button_confirm', cr)
            production.write({'state':'confirmed'}, context=context)
            # Call method to update moves attached on work-order process
            #self._update_workorder_lines(cr, uid, all_consume_moves, production.id, context=context)
        return shipment_id

    def copy(self, cr, uid, id, default=None, context=None):
        """
        -Process
            - blank workorder lines
        """
        if default is None: default = {}
        default.update({'workcenter_lines' : [],'moves_to_workorder':False,'parent_id':False,'procurement_generated':False,'scraped_qty':0.0,'scrap_order_id':False})
        return super(mrp_production, self).copy(cr, uid, id, default, context)

    def _find_production_id(self, cr, uid, workorder):
        """
        -Process
            -loop on workorder to find production_id
        -Return
            -Production Id
        """
        return workorder.production_id.id

    def to_find_next_wrkorder(self, cr, uid, production_id, last_workorder_id, last_workorder_seq, context=None):
        """
        -Process
            - find next stage of work-order by,
                - Production_id
                - sequence greater then equal from current work-order
                - next order id not current work-order id ;)
        -Return
            -[Next work-order Id, production_id]
        """
        context = context or {}
        workorder_line_obj = self.pool.get('mrp.production.workcenter.line')
        next_workorder_ids = workorder_line_obj.search(cr, uid, [('production_id', '=', production_id), ('sequence', '>=', last_workorder_seq), ('id', '!=', last_workorder_id),('state','!=','cancel')], order='sequence')
        return [next_workorder_ids and next_workorder_ids[0] or False, production_id]

    def next_stage_workorder(self, cr, uid, workorder_processmove_id, context=None):
        """
        -Process
            -get current workorder move process Id,
            -First find Workorder of that process move,
            -call function to find production id then,
            -call function to find next workorder
        - Return
            - next work-order
        """
        process_moves_obj = self.pool.get('stock.moves.workorder')
        # to find current work-order
        wrkorder_id = process_moves_obj.browse(cr, uid, workorder_processmove_id, context=context).workorder_id
        # to find production from that current work-order
        production_id = self._find_production_id(cr, uid, wrkorder_id)
        # to find next work-order
        return self.to_find_next_wrkorder(cr, uid, production_id, wrkorder_id.id, wrkorder_id.sequence, context=context)

    def _costs_generate(self, cr, uid, production):
        """ Calculates total costs at the end of the production.
        @param production: Id of production order.
        @return: Calculated amount.
        """
        amount = 0.0
        #amount_a = 0.0
        analytic_line_obj = self.pool.get('account.analytic.line')
        for wc_line in production.workcenter_lines:
            wc = wc_line.workcenter_id
            if wc.costs_journal_id and wc.costs_general_account_id:
                # Estimated Cost per hour
                value = wc_line.hour * wc.costs_hour
                # Actual Cost per hour
                #actual_value = wc_line.delay * wc.costs_hour
                account = wc.costs_hour_account_id.id
                if value and account:
                    amount += value
                    #amount_a += actual_value
                    analytic_line_obj.create(cr, uid, {
                        'name': wc_line.name + ' (H)',
                        'amount': wc_line.wo_actual_cost,
                        'planned_cost':wc_line.wo_planned_cost,
                        'account_id': account,
                        'production_id': wc_line.production_id and wc_line.production_id.id or False,
                        'general_account_id': wc.costs_general_account_id.id,
                        'journal_id': wc.costs_journal_id.id,
                        'ref': wc.code,
                        'product_id': wc.product_id.id,
                        'unit_amount': wc_line.hour,
                        'product_uom_id': wc.product_id and wc.product_id.uom_id.id or False
                    } )

                #We can't use cycle here. 
#                # Cost per cycle
#                value = wc_line.cycle * wc.costs_cycle
#                account = wc.costs_cycle_account_id.id
#                if value and account:
#                    amount += value
#                    amount_a += actual_value
#                    analytic_line_obj.create(cr, uid, {
#                        'name': wc_line.name+' (C)',
#                        'amount': amount_a,
#                        'account_id': account,
#                        'production_id': wc_line.production_id and wc_line.production_id.id or False,
#                        'planned_cost':value,
#                        'general_account_id': wc.costs_general_account_id.id,
#                        'journal_id': wc.costs_journal_id.id,
#                        'ref': wc.code,
#                        'product_id': wc.product_id.id,
#                        'unit_amount': wc_line.cycle,
#                        'product_uom_id': wc.product_id and wc.product_id.uom_id.id or False
#                    } )
        return amount

mrp_production()

class stock_moves_workorder(osv.osv):
    _name = 'stock.moves.workorder'

    def _semiproduct_calc(self, cr, uid, ids, name, args, context=None):
        result = dict([(id, {'product_factor': 0.0, 's_product_id':False,'s_total_qty': 0.0, 's_process_qty': 0.0, 's_accepted_qty': 0.0,'s_rejected_qty':0.0}) for id in ids])
        uom_obj = self.pool.get('product.uom')
        for smv in self.browse(cr, uid, ids, context=context):
            factor = 0.0
            if smv.workorder_id and smv.workorder_id.production_id:
                production = smv.workorder_id.production_id
                bom_point = smv.workorder_id.production_id.bom_id
                factor = uom_obj._compute_qty(cr, uid, production.product_uom.id, 1, bom_point.product_uom.id)
                factor = factor / bom_point.product_qty
                factor = factor / (bom_point.product_efficiency or 1.0)
                factor = rounding(factor, bom_point.product_rounding)
                if factor < bom_point.product_rounding:
                    factor = bom_point.product_rounding
                r_qty = 0.0
                for b in bom_point.bom_lines:
                    if smv.product_id.id == b.product_id.id:
                        r_qty = b.product_qty
                        break
                factor = factor * r_qty
            if factor == 0.0: return result
            result[smv.id]['product_factor'] = factor
            result[smv.id]['s_product_id'] = production.product_id.id
            result[smv.id]['s_total_qty'] = smv.total_qty / factor
            result[smv.id]['s_process_qty'] = smv.process_qty / factor
            result[smv.id]['s_accepted_qty'] = smv.accepted_qty / factor
            result[smv.id]['s_rejected_qty'] = smv.rejected_qty / factor
            result[smv.id]['s_uom_id'] = smv.workorder_id and smv.workorder_id.production_id and smv.workorder_id.production_id.product_uom.id or False
        return result

    _columns = {
        'name': fields.char('Name'),
        'workorder_id': fields.many2one('mrp.production.workcenter.line', 'WorkOrder'),
        'move_id': fields.many2one('stock.move', 'Move', readonly=True),
        #Problem when reallocated move to process
        #'prodlot_id': fields.related('move_id', 'prodlot_id', type='many2one', relation='stock.production.lot', string='Serial Number', readonly=True),
        'prodlot_id': fields.many2one('stock.production.lot', 'Serial Number', readonly=True), 
        'product_id': fields.many2one('product.product', 'Product', readonly=True),
        'uom_id': fields.many2one('product.uom', 'UoM', readonly=True),
        'start_date':fields.datetime('Start Date', help="Time when Product goes to start for workorder", readonly=True),
        'end_date':fields.datetime('End Date', help="Time when Product goes to finish or cancel for workorder", readonly=True),
        'total_qty': fields.float('Total Qty', digits_compute=dp.get_precision('Product Unit of Measure')),
        'process_qty': fields.float('InProcess Qty', digits_compute=dp.get_precision('Product Unit of Measure')),
        'accepted_qty': fields.float('Accept Qty', digits_compute=dp.get_precision('Product Unit of Measure')),
        'accepted_date':fields.datetime('Quality In-Date'),
        'rejected_qty': fields.float('Reject Qty', digits_compute=dp.get_precision('Product Unit of Measure')),

        'product_factor': fields.function(_semiproduct_calc, multi='semiproduct', type='float', string='Product Factor',digits_compute=dp.get_precision('Product Unit of Measure'),store=True),
        's_product_id': fields.function(_semiproduct_calc, multi='semiproduct', type='many2one', relation='product.product', string="Product"), 
        's_total_qty': fields.function(_semiproduct_calc, multi='semiproduct', type='float', string='Total Qty',digits_compute=dp.get_precision('Product Unit of Measure'),store=True),
        's_uom_id': fields.function(_semiproduct_calc, multi='semiproduct', type='many2one', relation='product.uom', string="UoM"),
        's_process_qty': fields.function(_semiproduct_calc, multi='semiproduct', type='float', string='InProcess Qty',digits_compute=dp.get_precision('Product Unit of Measure'),store=True),
        's_accepted_qty': fields.function(_semiproduct_calc, multi='semiproduct', type='float', string='Accept Qty',digits_compute=dp.get_precision('Product Unit of Measure'),store=True),
        's_rejected_qty': fields.function(_semiproduct_calc, multi='semiproduct', type='float', string='Reject Qty',digits_compute=dp.get_precision('Product Unit of Measure'),store=True),

        #'reason': fields.text('Reason'),
        'state': fields.selection(STATE_SELECTION, 'Status', readonly=True),

        #For outsourcing process
        'order_type': fields.related('workorder_id', 'order_type', type='selection', selection=[('in', 'Inside'), ('out', 'Outside')], string='Order Type', store=True), 
        'service_supplier_id': fields.many2one('res.partner', 'Supplier',domain=[('supplier','=',True)]),
        'po_order_id': fields.many2one('purchase.order', 'Service Order'),
    }

    _defaults = {
        'state': 'draft'
        }

    def button_to_draft(self, cr, uid, ids , context=None):
        return True

    def button_to_start(self, cr, uid, ids , context=None):
        """
        -Process
            -Raw material Process
                -Update State, Start Date, Process Qty
            -Start WorkOrder Also
        """
        #All process moves have its own PO, DO, INWORD
        currnt_data = self.browse(cr, uid, ids[0], context=context)
        if currnt_data.order_type == 'out':
            currnt_data.workorder_id.dummy_button()
            if not currnt_data.service_supplier_id: raise osv.except_osv(_('Warning!!!'),_('1)Define supplier on line or \n2)Click "⟳ (Update)" button to save the record!'))
            return self._call_service_order(cr, uid, ids, context=context)

        wf_service = netsvc.LocalService("workflow")
        self.write(cr, uid, ids, {
                                  'state':'in_progress',
                                  #'start_date':time.strftime(DEFAULT_SERVER_DATETIME_FORMAT),
                                  'process_qty':currnt_data.total_qty
                                  })
        #Start Work-Order Also.
        wf_service.trg_validate(uid, 'mrp.production.workcenter.line', currnt_data.workorder_id.id, 'button_start_working', cr)
        #currnt_data.workorder_id.action_start_working(context=context)
        return True

    def _call_service_order(self, cr, uid, ids, context=None):
        """
        Process
            -call wizard to ask for service order
        """
        # Get service order wizard
        models_data = self.pool.get('ir.model.data')
        dummy, form_view = models_data.get_object_reference(cr, uid, 'l10n_in_mrp_subcontract', 'view_generate_service_order')

        return {
            'name': _('Service Order'),
            'view_type': 'form',
            'view_mode': 'form',
            'context':context,
            'res_model': 'generate.service.order',
            'views': [(form_view or False, 'form')],
            'type': 'ir.actions.act_window',
            'target':'new'
        }


    def button_to_reject(self, cr, uid, ids , context=None):
        """
        -Process
            -Call wizard for quantity for rejection process
        -Return
            -Open Rejection Wizard
        """
        context = context or {}
        models_data = self.pool.get('ir.model.data')
        # Get Rejected wizard
        dummy, form_view = models_data.get_object_reference(cr, uid, 'l10n_in_mrp_subcontract', 'view_process_qty_to_update_reject')
        currnt_data = self.browse(cr, uid, ids[0], context=context)
        factor = currnt_data.product_factor
        context.update({
                        'total_qty':currnt_data.total_qty,
                        'product_id':currnt_data.product_id.id,
                        'already_rejected_qty': currnt_data.rejected_qty,
                        'process_qty': currnt_data.process_qty,


                        'product_factor':factor,
                        's_product_id': currnt_data.s_product_id and currnt_data.s_product_id.id or False,
                        's_process_qty': factor <> 0.0 and currnt_data.process_qty / currnt_data.product_factor or currnt_data.process_qty,

                        })

        return {
            'name': _('Rejected Quantity'),
            'view_type': 'form',
            'view_mode': 'form',
            'context':context,
            'res_model': 'process.qty.to.update.reject',
            'views': [(form_view or False, 'form')],
            'type': 'ir.actions.act_window',
            'target':'new'
        }

    def button_to_finish(self, cr, uid, ids , context=None):
        """
        -Process
            -Call wizard for quantity for finished process
        -Return
            -Open finished Wizard
        """
        context = context or {}
        models_data = self.pool.get('ir.model.data')
        # Get Accepted wizard
        dummy, form_view = models_data.get_object_reference(cr, uid, 'l10n_in_mrp_subcontract', 'view_process_qty_to_finished')
        currnt_data = self.browse(cr, uid, ids[0], context=context)
        factor = currnt_data.product_factor
        context.update({
                        'already_accepted_qty':currnt_data.accepted_qty,
                        'total_qty':currnt_data.total_qty,
                        'product_id':currnt_data.product_id.id,
                        'process_qty': currnt_data.process_qty,

                        'product_factor':factor,
                        's_product_id': currnt_data.s_product_id and currnt_data.s_product_id.id or False,
                        's_process_qty': factor <> 0.0 and currnt_data.process_qty / currnt_data.product_factor or currnt_data.process_qty,
                        })

        return {
            'name': _('Accept Quantity'),
            'view_type': 'form',
            'view_mode': 'form',
            'context':context,
            'res_model': 'process.qty.to.finished',
            'views': [(form_view or False, 'form')],
            'type': 'ir.actions.act_window',
            'target':'new'
        }

    def button_to_consume(self, cr, uid, ids , context=None):
        """
        -Process
            -Call wizard for quantity for consuming process
        -Return
            -Open consume Wizard
        """
        context = context or {}
        models_data = self.pool.get('ir.model.data')
        # Get Accepted wizard
        dummy, form_view = models_data.get_object_reference(cr, uid, 'l10n_in_mrp_subcontract', 'view_process_qty_to_consume')
        currnt_data = self.browse(cr, uid, ids[0], context=context)
        context.update({
                        'already_accepted_qty':currnt_data.accepted_qty,
                        'total_qty':currnt_data.total_qty,
                        'product_id':currnt_data.product_id.id,
                        'process_qty': currnt_data.process_qty,
                        })

        return {
            'name': _('Consume Quantity'),
            'view_type': 'form',
            'view_mode': 'form',
            'context':context,
            'res_model': 'qty.to.consume',
            'views': [(form_view or False, 'form')],
            'type': 'ir.actions.act_window',
            'target':'new'
        }

stock_moves_workorder()

class stock_moves_rejection(osv.osv):
    _name = 'stock.moves.rejection'
    _columns = {
        'name': fields.char('Name'),
        'rejected_workorder_id': fields.many2one('mrp.production.workcenter.line', 'WorkOrder'),
        'move_id': fields.many2one('stock.move', 'Move', readonly=True),
        'prodlot_id': fields.related('move_id', 'prodlot_id', type='many2one', relation='stock.production.lot', string='Serial Number', readonly=True),
        'rejected_location_id': fields.many2one('stock.location', 'Rejection Location', readonly=True),
        'rejected_from_process_move_id': fields.many2one('stock.moves.workorder', 'Rejection From', readonly=True),
        'product_id': fields.many2one('product.product', 'Product', readonly=True),
        'uom_id': fields.many2one('product.uom', 'UoM', readonly=True),
        'rejected_date':fields.datetime('Rejected Date', readonly=True),
        'reallocate_date':fields.datetime('Reallocate Date', readonly=True),
        'rejected_qty': fields.float('Rejected Quantity', digits_compute=dp.get_precision('Product Unit of Measure')),
        's_product_id': fields.many2one('product.product', 'Product', readonly=True),
        's_rejected_qty': fields.float('Rejected Quantity', digits_compute=dp.get_precision('Product Unit of Measure')),
        's_uom_id': fields.many2one('product.uom', 'UoM', readonly=True),
        'reason': fields.text('Reason'),
        'state': fields.selection([('rejected', 'Rejected')], 'Status', readonly=True),
        'is_reallocate':  fields.boolean('Re-Allocated?')
    }

    def button_to_reallocate(self, cr, uid, ids, context=None):
        """
        -Process
            -Call wizard for product,
                -Real stock availability,
                -you can select work-order to move,
                -create directly moves to work-order
        -Return
            -Open Reallocate Wizard
        """
        context = context or {}
        models_data = self.pool.get('ir.model.data')
        # Get Accepted wizard
        dummy, form_view = models_data.get_object_reference(cr, uid, 'l10n_in_mrp_subcontract', 'view_reallocate_rejected_move')
        currnt_data = self.browse(cr, uid, ids[0], context=context)
        context.update({
                        'total_qty':currnt_data.rejected_qty,
                        'product_id':currnt_data.product_id.id,
                        'process_move_id': currnt_data.rejected_from_process_move_id.id,
                        'rejected_workorder_id':currnt_data.rejected_workorder_id and currnt_data.rejected_workorder_id.id or False
                        })

        return {
            'name': _('Re-Allocate Product Quantity To Work-Order'),
            'view_type': 'form',
            'view_mode': 'form',
            'context':context,
            'res_model': 'reallocate.rejected.move',
            'views': [(form_view or False, 'form')],
            'type': 'ir.actions.act_window',
            'target':'new'
        }

stock_moves_rejection()

class mrp_production_workcenter_line(osv.osv):


    def _mrp_wo_costing(self, cr, uid, ids, name, args, context=None):
        """
        Process
            -Planned Cost = cost hour * planned time
            -Actual Cost = cost hour * actual time
        """

        def float_time_convert(float_val):
            factor = float_val < 0 and -1 or 1
            val = abs(float_val)
            return (factor * int(math.floor(val)), int(round((val % 1) * 60)))

        result = dict([(id, {'wo_planned_cost': 0.0, 'wo_actual_cost': 0.0,'operator_efficiency':0.0}) for id in ids])
        for wo in self.browse(cr, uid, ids, context=context):
            wo_planned_cost = wo_actual_cost = operator_efficiency = 0.0
            #if wo.state == 'cancel': continue
            wo_planned_cost += round(wo.hour,2) * wo.workcenter_id.costs_hour
            wo_actual_cost += round(wo.delay,2) * wo.workcenter_id.costs_hour
            p_hour,p_min = float_time_convert(wo.hour)
            a_hour,a_min = float_time_convert(wo.delay)
            p_seconds = p_hour * 3600 + p_min * 60
            a_seconds = a_hour * 3600 + a_min * 60
            if a_seconds > 0:
                operator_efficiency =  (float(p_seconds) / float(a_seconds)) * 100
        result[wo.id]['wo_planned_cost'] = wo_planned_cost
        result[wo.id]['wo_actual_cost'] = wo_actual_cost
        result[wo.id]['operator_efficiency'] = int(operator_efficiency)
        return result

#    def onchange_log_entry(self,cr, uid, ids, log_entry_ids, context=None):
#        res = {'value':{'delay':0.0}}
#        diff_list = []
#        if not log_entry_ids: return {}
#        for rec in log_entry_ids:
#            if rec and rec[2]:
#                time_df = (datetime.strptime(rec[2]['end_date'],'%Y-%m-%d %H:%M:%S')-datetime.strptime(rec[2]['start_date'],'%Y-%m-%d %H:%M:%S')).total_seconds()
#                diff_list.append(time_df)
#        if diff_list:
#            res['value'].update({'delay':sum(diff_list)})
#        return res

    def _count_log_delay(self, cr, uid, ids, name, args, context=None):
        """
        Process
            -count log entry delay and set to actual time
        """

        result = dict([(id, {'log_delay': 0.0}) for id in ids])
        for wo in self.browse(cr, uid, ids, context=context):
            delay = 0.0
            for rec in wo.log_entry_ids:
                dt_df = datetime.strptime(rec.end_date,'%Y-%m-%d %H:%M:%S') - datetime.strptime(rec.start_date,'%Y-%m-%d %H:%M:%S')
                delay += dt_df.days * 24
                delay += dt_df.seconds / float(60*60)
            result[wo.id]['log_delay'] = delay
            cr.execute(""" UPDATE mrp_production_workcenter_line SET delay = %s WHERE id = %s"""%(delay, wo.id))
        return result

    def _calculated_hour(self, cr, uid, wo, qty, context=None):
        wc = wo.workcenter_id
        hour = float((wo.hour_nbr * qty + ((wc.time_start or 0.0)+(wc.time_stop or 0.0))) / float(wc.time_efficiency or 1.0))
        return hour

    def _mrp_rejctd_qty(self, cr, uid, ids, name, args, context=None):
        """
        Process
            -count log entry delay and set to actual time
        """

        result = dict([(id, {'t_rejection_qty': 0.0}) for id in ids])
        for wo in self.browse(cr, uid, ids, context=context):
            qty = 0.0
            if wo.production_id:
                cr.execute("""  SELECT sum(s_rejected_qty) FROM stock_moves_rejection 
                                WHERE rejected_workorder_id in 
                                (SELECT id FROM mrp_production_workcenter_line 
                                WHERE sequence < %s 
                                AND production_id = %s
                                ) """%(wo.sequence, wo.production_id.id))
                qty = cr.fetchone()[0]
            rejected_qty = qty or 0.0
            result[wo.id]['t_rejection_qty'] = rejected_qty
            planned_qty = wo.production_id.product_qty - rejected_qty
            hour = self._calculated_hour(cr, uid, wo, planned_qty, context=context)
            planned_cost = round(wo.hour,2) * wo.workcenter_id.costs_hour
            cr.execute(""" UPDATE mrp_production_workcenter_line SET hour = %s WHERE id = %s"""%(hour, wo.id))
            
            #Here we cannot call write method to update auto next workorder.
#            cr.execute(""" SELECT id FROM mrp_production_workcenter_line 
#                            WHERE sequence > %s 
#                            AND production_id = %s """%(wo.sequence, wo.production_id.id)
#                                )
#            update_all = [x[0] for x in cr.fetchall() if x]
#            self.dummy_button(cr, uid, update_all, context)
        return result


    _inherit = 'mrp.production.workcenter.line'
    _columns = {
        'sequence': fields.integer('Sequence', required=True, help="Gives the sequence order when displaying a list of work orders.",readonly=True, states={'draft':[('readonly', False)]}),
        'user_id': fields.many2one('res.users', 'Responsible',readonly=False, states={'done':[('readonly', True)]}),
        'moves_workorder': fields.one2many('stock.moves.workorder', 'workorder_id', 'Raw Material To Process'),
        'moves_rejection': fields.one2many('stock.moves.rejection', 'rejected_workorder_id', 'Rejected Raw Material'),
        #For labour log entry.
        'log_entry_ids': fields.one2many('log.entry','workorder_id', 'Log Entry'),

        'hour': fields.float('Est.Time(HH:MM)', digits=(16,2),readonly=True, states={'draft':[('readonly', False)]}),
        'delay': fields.float('Actual Time(HH:MM)',help="The elapsed time between day to day log entry by user",readonly=True),
        'log_delay': fields.function(_count_log_delay, method=True, multi='log', type='float', string='Workorder Actual Cost',store=True),
        #'service_product_id': fields.many2one('product.product', 'Service Product'),
        #'service_supplier_id': fields.many2one('res.partner', 'Partner',domain=[('supplier','=',True)]),
        #'service_description': fields.text('Description'),
        #'po_order_id': fields.many2one('purchase.order', 'Service Order'),
        'order_type': fields.selection([('in', 'Inside'), ('out', 'Outside')], 'WorkOrder Process', readonly=True, states={'draft':[('readonly', False)]}),
        'workcenter_id': fields.many2one('mrp.workcenter', 'Work Center', required=True , readonly=True, states={'draft':[('readonly', False)]}),
        'temp_date_finished':fields.related('date_finished', type="datetime",store=True),

        'currency_id': fields.related('production_id', 'currency_id', type="many2one", relation="res.currency", string="Currency", readonly=True),
        'wo_planned_cost': fields.function(_mrp_wo_costing, multi='cost', type='float', string='Workorder Planned Cost'),
        'wo_actual_cost': fields.function(_mrp_wo_costing, multi='cost', type='float', string='Workorder Actual Cost',store=True),
        'operator_efficiency': fields.function(_mrp_wo_costing, multi='cost', type='integer', string='Operator Efficiency(%)',group_operator="avg"),
        't_rejection_qty': fields.function(_mrp_rejctd_qty, multi='rj', type='float', string='Rejection Qty'),
        'hour_nbr': fields.float('Line Hour'),

    }
    _sql_constraints = [('sequence_uniq', 'unique(sequence, production_id)', "You cannot assign same sequence on current production order")] 
    _sql_constraints = [('wo_date_greater','check(date_finished >= date_start)','Error ! Stop Date cannot be set before Beginning Date.')] 
    _defaults = {
        'order_type': 'in',
        'hour_nbr':0.0
        }

    def onchange_planned_cost(self,cr, uid, ids, planned_hour, actual_hour, actual_cost, workcenter_id, context=None):
        """
        Process
            -In case , Added manually work-order for production at that time,
                It will update all fields according to planned hour.
        """
        workcenter_obj = self.pool.get('mrp.workcenter')
        if not workcenter_id:
            return {}

        wdata = workcenter_obj.browse(cr, uid, workcenter_id)

        def float_time_convert(float_val):
            factor = float_val < 0 and -1 or 1
            val = abs(float_val)
            return (factor * int(math.floor(val)), int(round((val % 1) * 60)))

        operator_efficiency = 0
        wo_planned_cost = planned_hour * wdata.costs_hour
        wo_actual_cost = actual_hour * wdata.costs_hour
        p_hour,p_min = float_time_convert(planned_hour)
        a_hour,a_min = float_time_convert(actual_hour)
        p_seconds = p_hour * 3600 + p_min * 60
        a_seconds = a_hour * 3600 + a_min * 60
        if a_seconds > 0:
            operator_efficiency =  (float(p_seconds) / float(a_seconds)) * 100
        return {'value':{'wo_planned_cost':wo_planned_cost,'wo_actual_cost':wo_actual_cost,'operator_efficiency':operator_efficiency}}


    def modify_production_order_state(self, cr, uid, ids, action):
        """
        -Process
            -overwrite this function to only ignore cancel workorder ;)
        -Return
            -As it is
        """
        wf_service = netsvc.LocalService("workflow")
        prod_obj_pool = self.pool.get('mrp.production')
        oper_obj = self.browse(cr, uid, ids)[0]
        prod_obj = oper_obj.production_id
        if action == 'start':
            if prod_obj.state =='confirmed':
                prod_obj_pool.force_production(cr, uid, [prod_obj.id])
                wf_service.trg_validate(uid, 'mrp.production', prod_obj.id, 'button_produce', cr)
            elif prod_obj.state =='ready':
                wf_service.trg_validate(uid, 'mrp.production', prod_obj.id, 'button_produce', cr)
            elif prod_obj.state =='in_production':
                return
            else:
                raise osv.except_osv(_('Error!'),_('Manufacturing order cannot be started in state "%s"!') % (prod_obj.state,))
        else:
            oper_ids = self.search(cr,uid,[('production_id','=',prod_obj.id)])
            obj = self.browse(cr,uid,oper_ids)
            flag = True
            for line in obj:
                #Update code for ignore cancel workorder
                if line.state != 'done' and line.state != 'cancel':
                    flag = False
            if flag:
                for production in prod_obj_pool.browse(cr, uid, [prod_obj.id], context= None):
                    if production.move_lines or production.move_created_ids:
                        prod_obj_pool.action_produce(cr,uid, production.id, production.product_qty, 'consume_produce', context = None)
                wf_service.trg_validate(uid, 'mrp.production', oper_obj.production_id.id, 'button_produce_done', cr)
        return

    def unlink(self, cr, uid, ids, context=None):
        """
        -Process
            - Raise Warning, if they have raw material in-process or rejected quantity assigned to it
        """
        for wrkorder in self.browse(cr, uid, ids, context=context):
            if wrkorder.moves_workorder or wrkorder.moves_rejection:
                raise osv.except_osv(_('Invalid Action!'), _('Cannot delete a work-order if they have raw material in-process or rejected quantity assigned to it.'))
        return super(mrp_production_workcenter_line, self).unlink(cr, uid, ids, context=context)

    def _check_for_process_none_qty(self, cr, uid, ids, process='start', context=None):
        """
        -Process
            - check process moves available then and then only start any workorder
        -Return
            - Raise Warning, if None in process moves
        """
        process_moves = self.pool.get('stock.moves.workorder')
        if not process_moves.search(cr, uid, [('workorder_id', '=', ids[0])], context=context):
            raise osv.except_osv(_('Raw Material not found!'), _('You cannot %s work-order without any raw material') % (process))
        return True

#    def _call_service_order(self, cr, uid, ids, context=None):
#        """
#        Process
#            -call wizard to ask for service order
#        """
#        # Get service order wizard
#        models_data = self.pool.get('ir.model.data')
#        dummy, form_view = models_data.get_object_reference(cr, uid, 'l10n_in_mrp_subcontract', 'view_generate_service_order')
#
#        return {
#            'name': _('Service Order'),
#            'view_type': 'form',
#            'view_mode': 'form',
#            'context':context,
#            'res_model': 'generate.service.order',
#            'views': [(form_view or False, 'form')],
#            'type': 'ir.actions.act_window',
#            'target':'new'
#        }

    def action_draft(self, cr, uid, ids, context=None):
        """ 
        -Return
            -same super call
        """
        return super(mrp_production_workcenter_line, self).action_draft(cr, uid, ids, context=context)

    def action_to_start_working(self, cr, uid, ids, context=None):
        """ 
        -Process
            -if order type == 'service':
                - Open wizard to ask to generate service order
                - Generate Service Order for Service type product
            -call funtion to check process moves available then and then only start any workorder
        -Return
            -same super call
        """
        context = context or {}

#        This has been moved to process moves because all process moves have its own PO, DO, INWORD
#        wrk_rec = self.browse(cr, uid, ids[0], context=context)
#        if wrk_rec.order_type == 'out':
#            return self._call_service_order(cr, uid, ids, context=context)

        self._check_for_process_none_qty(cr, uid, ids, 'start', context=context)
        self.modify_production_order_state(cr, uid, ids, 'start')
        #self.write(cr, uid, ids, {'state':'startworking', 'date_start': time.strftime('%Y-%m-%d %H:%M:%S')}, context=context)
        #Always human dependance so be versatile not systamatic;).
        self.write(cr, uid, ids, {'state':'startworking'}, context=context)
        return True

    def action_start_working(self, cr, uid, ids, context=None):
        """ 
        Process
            -Just removed date updation when workcenter started.
        """
        self.modify_production_order_state(cr, uid, ids, 'start')
        #self.write(cr, uid, ids, {'state':'startworking', 'date_start': time.strftime('%Y-%m-%d %H:%M:%S')}, context=context)
        self.write(cr, uid, ids, {'state':'startworking'}, context=context)
        return True

    def _check_out_all_lines(self, cr, uid, current_data, context=None):
        """
        Process(OutSide)
            -Check to finished all process move lines
        """
        context = context or {}
        for lines in current_data.moves_workorder:
            if lines.state in ('draft','in_progress'):
                raise osv.except_osv(_('Couldn"t finish workorder!'), _('You must finish all work-order process lines first.'))
        return True

    def action_finish(self, cr, uid, ids, context=None):
        """ 
        -Process
            -find next workorder id which all moves goes to that workorder
            -create list of all process moves to finished
        -Return
            -Wizard to ask for all in once to process ?
        Note: Super method call on apply button on wizard ;)
              First check all lines of workorder are finished or not.
        """
        context = context or {}
        models_data = self.pool.get('ir.model.data')
        mrp_obj = self.pool.get('mrp.production')
        # Get Accepted wizard
        dummy, form_view = models_data.get_object_reference(cr, uid, 'l10n_in_mrp_subcontract', 'view_all_in_once_qty_to_finished')
        currnt_data = self.browse(cr, uid, ids[0], context=context)
        #if currnt_data.order_type == 'out':
        #Now check for both types either "IN" or "OUT"
        self._check_out_all_lines(cr, uid, currnt_data, context)
        next_stage_workorder_id = mrp_obj.to_find_next_wrkorder(cr, uid, currnt_data.production_id.id, ids[0], currnt_data.sequence, context=context)
        all_process_moves_ids = []
        for find_pm in currnt_data.moves_workorder:
            if find_pm.state not in ('finished', 'rejected','consumed'):
                all_process_moves_ids.append({
                                            'select':True,
                                            'process_move_id':find_pm.id,
                                            'product_id': find_pm.product_id and find_pm.product_id.id or False,
                                            'accepted_qty': find_pm.accepted_qty,
                                            'total_qty': find_pm.total_qty - (find_pm.accepted_qty + find_pm.rejected_qty),
                                            'state': find_pm.state
                                             })

        context.update({
                        'all_process_moves_ids':all_process_moves_ids,
                        'next_stage_workorder_id':next_stage_workorder_id
                        })

        return {
            'name': _('All In Once Quantity To Finish'),
            'view_type': 'form',
            'view_mode': 'form',
            'context':context,
            'res_model': 'all.in.once.qty.to.finished',
            'views': [(form_view or False, 'form')],
            'type': 'ir.actions.act_window',
            'target':'new'
        }

    def action_cancelled(self, cr, uid, ids, context=None):
        """ 
        -Process
            -find next workorder id which all moves goes to that workorder
            -create list of all process moves goes to cancel
        -Return
            -Wizard to ask for all in once to process ?
        Note: Super method call on apply button on wizard ;)
        """
        context = context or {}
        models_data = self.pool.get('ir.model.data')
        mrp_obj = self.pool.get('mrp.production')
        # Get Cancel wizard
        dummy, form_view = models_data.get_object_reference(cr, uid, 'l10n_in_mrp_subcontract', 'view_all_in_once_qty_to_cancelled')
        currnt_data = self.browse(cr, uid, ids[0], context=context)
        next_stage_workorder_id = mrp_obj.to_find_next_wrkorder(cr, uid, currnt_data.production_id.id, ids[0], currnt_data.sequence, context=context)
        all_process_moves_cancel_ids = []
        for find_pm in currnt_data.moves_workorder:
            if find_pm.state not in ('finished', 'rejected','consumed'):
                all_process_moves_cancel_ids.append({
                                            'select':True,
                                            'process_move_id':find_pm.id,
                                            'product_id': find_pm.product_id and find_pm.product_id.id or False,
                                            'accepted_qty': find_pm.accepted_qty,
                                            'total_qty': find_pm.total_qty - (find_pm.accepted_qty + find_pm.rejected_qty),
                                            'state': find_pm.state
                                             })

        context.update({
                        'all_process_moves_cancel_ids':all_process_moves_cancel_ids,
                        'next_stage_workorder_id':next_stage_workorder_id
                        })

        return {
            'name': _('All In Once Quantity To Cancel'),
            'view_type': 'form',
            'view_mode': 'form',
            'context':context,
            'res_model': 'all.in.once.qty.to.cancelled',
            'views': [(form_view or False, 'form')],
            'type': 'ir.actions.act_window',
            'target':'new'
        }

    def action_pause(self, cr, uid, ids, context=None):
        """ 
        -Process
            -call funtion to check process moves available then and then only start any workorder
        -Return
            -same super call
        """
        self._check_for_process_none_qty(cr, uid, ids, 'Pause', context=context)
        return super(mrp_production_workcenter_line, self).action_pause(cr, uid, ids, context=context)

    def action_resume(self, cr, uid, ids, context=None):
        """ 
        -Process
            -call funtion to check process moves available then and then only start any workorder
        -Return
            -same super call
        """
        self._check_for_process_none_qty(cr, uid, ids, 'Resume', context=context)
        return super(mrp_production_workcenter_line, self).action_resume(cr, uid, ids, context=context)

    def add_consume_product(self, cr, uid, ids, context=None):
        """
        -Process
            -add consume line to Product to consume
            -Done this consume line to consume
            -add line to workorder for consume 
        """
        context = context or {}
        models_data = self.pool.get('ir.model.data')
        # Get consume wizard
        dummy, form_view = models_data.get_object_reference(cr, uid, 'l10n_in_mrp_subcontract', 'view_add_rawmaterial_to_consume')
        current = self.browse(cr, uid, ids[0], context=context).production_id
        finish_move_id = False

        #To find produce move
        for move in current.move_created_ids:
            finish_move_id = move.id
            break
        if not finish_move_id:
            for move in current.move_created_ids2:
                finish_move_id = move.id
                break

        context.update({
                        'finish_move_id': finish_move_id,
                        })
        return {
            'name': _('Add consume Material to Work-Order'),
            'view_type': 'form',
            'view_mode': 'form',
            'context':context,
            'res_model': 'add.rawmaterial.to.consume',
            'views': [(form_view or False, 'form')],
            'type': 'ir.actions.act_window',
            'target':'new'
        }

    def dummy_button(self, cr, uid, ids, context=None):
        """
        -Process
            -Update process moves to work-order
        """
        return True

    def create_service_order(self, cr, uid, ids , context=None):
        return True

    #Added float_time widget to auto converted
    def write(self, cr, uid, ids, vals, context=None, update=True):
        """
        -process
            -Update delay, depends on start date and finished date dynamically.
        """
        if vals.get('date_start', False) or vals.get('date_finished', False):
            date_start, date_finished = False,False
            if isinstance(ids, (int, long)):
                ids = [ids]
            if vals.get('date_start'):
                date_finished = self.browse(cr, uid, ids[0], context=context).date_finished
                date_start = vals['date_start']
            if vals.get('date_finished'):
                date_start = self.browse(cr, uid, ids[0], context=context).date_start
                date_finished = vals['date_finished']
            if date_start and date_finished:
                delay = 0.0
                start = datetime.strptime(date_start,'%Y-%m-%d %H:%M:%S')
                finished = datetime.strptime(date_finished,'%Y-%m-%d %H:%M:%S')
                delay += (finished-start).days * 24
                delay += (finished-start).seconds / float(60*60)
#                days = ((finished-start).days * 24) + ((finished-start).seconds) // 3600
#                minite = (((finished-start).seconds%3600) / float(60))/100
                #vals.update({'delay': delay})
        return super(mrp_production_workcenter_line, self).write(cr, uid, ids, vals, context=context)

    def action_done(self, cr, uid, ids, context=None):
        """ 
        -Process
            -Delay shaw in HH:MM format with diffrent calculation
        """
        date_now = time.strftime('%Y-%m-%d %H:%M:%S')
        obj_line = self.browse(cr, uid, ids[0])

        delay = 0.0
        if obj_line.date_start and obj_line.date_finished:
            date_start = datetime.strptime(obj_line.date_start,'%Y-%m-%d %H:%M:%S')
            date_finished = datetime.strptime(obj_line.date_finished,'%Y-%m-%d %H:%M:%S')
            delay += (date_finished-date_start).days * 24
            delay += (date_finished-date_start).seconds / float(60*60)

#        days = ((date_finished-date_start).days * 24) + ((date_finished-date_start).seconds) // 3600
#        minite = (((date_finished-date_start).seconds%3600) / float(60))/100
#        delay = days + minite

        self.write(cr, uid, ids, {'state':'done','delay':delay}, context=context)
        #self.write(cr, uid, ids, {'state':'done', 'date_finished': date_now,'delay':delay}, context=context)
        #IMPORTANTE, Cannot done production order after processing of done workorder.
        #self.modify_production_order_state(cr,uid,ids,'done')

        return True


mrp_production_workcenter_line()


class mrp_routing_workcenter(osv.osv):
    _inherit = 'mrp.routing.workcenter'
    _columns = {
#        'service_supplier_id': fields.many2one('res.partner', 'Partner',domain=[('supplier','=',True)]),
#        'service_description': fields.text('Description'),
#        'po_order_id': fields.many2one('purchase.order', 'Service Order'),
        'order_type': fields.selection([('in', 'Inside'), ('out', 'Outside')], 'WorkOrder Process'),
    }
    _defaults = {'order_type':'in'}

mrp_routing_workcenter()

class mrp_bom(osv.osv):
    _inherit = 'mrp.bom'


    def _bom_explode(self, cr, uid, bom, factor, properties=None, addthis=False, level=0, routing_id=False):
        """ Finds Products and Work Centers for related BoM for manufacturing order.
        @param bom: BoM of particular product.
        @param factor: Factor of product UoM.
        @param properties: A List of properties Ids.
        @param addthis: If BoM found then True else False.
        @param level: Depth level to find BoM lines starts from 10.
        @return: result: List of dictionaries containing product details.
                 result2: List of dictionaries containing Work Center details.
        """

        routing_obj = self.pool.get('mrp.routing')
        factor = factor / (bom.product_efficiency or 1.0)
        max_rounding = max(bom.product_rounding, bom.product_uom.rounding)
        factor = rounding(factor, max_rounding)
        if factor < max_rounding:
            factor = max_rounding
        result = []
        result2 = []
        phantom = False
        if bom.type == 'phantom' and not bom.bom_lines:
            newbom = self._bom_find(cr, uid, bom.product_id.id, bom.product_uom.id, properties)

            if newbom:
                res = self._bom_explode(cr, uid, self.browse(cr, uid, [newbom])[0], factor*bom.product_qty, properties, addthis=True, level=level+10)
                result = result + res[0]
                result2 = result2 + res[1]
                phantom = True
            else:
                phantom = False
        if not phantom:
            if addthis and not bom.bom_lines:
                result.append(
                {
                    'name': bom.product_id.name,
                    'product_id': bom.product_id.id,
                    'product_qty': bom.product_qty * factor,
                    'product_uom': bom.product_uom.id,
                    'product_uos_qty': bom.product_uos and bom.product_uos_qty * factor or False,
                    'product_uos': bom.product_uos and bom.product_uos.id or False,
                })
            routing = (routing_id and routing_obj.browse(cr, uid, routing_id)) or bom.routing_id or False
            if routing:
                for wc_use in routing.workcenter_lines:
                    wc = wc_use.workcenter_id
                    #change here , suppose workcentere put 0.0 capicity then ?
                    #d, m = divmod(factor, wc_use.workcenter_id.capacity_per_cycle)
                    d, m = divmod(factor, wc_use.workcenter_id.capacity_per_cycle or 1.0)
                    mult = (d + (m and 1.0 or 0.0))
                    cycle = mult * wc_use.cycle_nbr
                    result2.append({
                        'name': tools.ustr(wc_use.name) + ' - '  + tools.ustr(bom.product_id.name),
                        'workcenter_id': wc.id,
                        'order_type':wc_use.order_type,
                        'sequence': level+(wc_use.sequence or 0),
                        'cycle': cycle,
                        'hour_nbr':wc_use.hour_nbr,
                        #'hour': float(wc_use.hour_nbr*mult + ((wc.time_start or 0.0)+(wc.time_stop or 0.0)+cycle*(wc.time_cycle or 0.0)) * (wc.time_efficiency or 1.0)),
                        #Estimatated Hours = (Before P Stat+Before P Stop + total hours(define in routing) ) / effieciency
                        #Engineering manufacturing company dosent consider cycle loop, Its alwys work on hours basis.
                        'hour': float((wc_use.hour_nbr*mult + ((wc.time_start or 0.0)+(wc.time_stop or 0.0))) / float(wc.time_efficiency or 1.0))
                    })
            for bom2 in bom.bom_lines:
                res = self._bom_explode(cr, uid, bom2, factor, properties, addthis=True, level=level+10)
                result = result + res[0]
                result2 = result2 + res[1]
        return result, result2

mrp_bom()

class log_entry(osv.osv):
    """ Log entry fill by labours """
    _name = 'log.entry'


    def onchange_date(self,cr, uid, ids, start_date, end_date, context=None):
        """
        Process
            - set Log Time on onchange
        """
        delay = 0.0
        if start_date and end_date:
            dt_df = datetime.strptime(end_date,'%Y-%m-%d %H:%M:%S') - datetime.strptime(start_date,'%Y-%m-%d %H:%M:%S')
            delay += dt_df.days * 24
            delay += dt_df.seconds / float(60*60)
        return {'value': {'log_time': delay}}

    def create(self, cr, uid, vals, context=None):
        """
        Procecss
            -Log time shawn in HH:MM on creation
        """
        if vals and vals.get('start_date') and vals.get('end_date'):
            dt_df = datetime.strptime(vals['end_date'],'%Y-%m-%d %H:%M:%S') - datetime.strptime(vals['start_date'],'%Y-%m-%d %H:%M:%S')
            delay = dt_df.days * 24
            delay += dt_df.seconds / float(60*60)
            vals.update({'log_time':delay})
        return super(log_entry, self).create(cr, uid, vals, context=context)

    def write(self, cr, uid, ids, vals, context=None):
        """
        Procecss
            -Log time shawn in HH:MM on write object
        """
        if vals.get('start_date', False) or vals.get('end_date', False):
            start_date, end_date = False,False
            if isinstance(ids, (int, long)):
                ids = [ids]
            if vals.get('start_date'):
                end_date = self.browse(cr, uid, ids[0], context=context).end_date
                start_date = vals['start_date']
            if vals.get('end_date'):
                start_date = self.browse(cr, uid, ids[0], context=context).start_date
                end_date = vals['end_date']
            if start_date and end_date:
                delay = 0.0
                start = datetime.strptime(start_date,'%Y-%m-%d %H:%M:%S')
                finished = datetime.strptime(end_date,'%Y-%m-%d %H:%M:%S')
                delay += (finished-start).days * 24
                delay += (finished-start).seconds / float(60*60)
                vals.update({'log_time': delay})
        return super(log_entry, self).write(cr, uid, ids, vals, context=context)

    _columns = {
        'employee_id': fields.many2one('hr.employee','Employee',required=True),
        'workorder_id': fields.many2one('mrp.production.workcenter.line', 'WorkOrder'),
        'start_date':fields.datetime('Start Date', required=True),
        'end_date':fields.datetime('End Date', required=True),
        'log_time': fields.float('Time(HH:MM)',readonly=True),
        'qty': fields.float('Qty',required=True)
    }

    _sql_constraints = [('wo_date_greater_log','check(end_date >= start_date)','Log Entry Error ! End Date cannot be set before Starting Date.')]

log_entry()


class mrp_routing(osv.osv):
    """ Max size given to code """
    _inherit = 'mrp.routing'
    _columns = {
        'code': fields.char('Code', size=256),
    }
mrp_routing()
# vim:expandtab:smartindent:tabstop=4:softtabstop=4:shiftwidth=4:
