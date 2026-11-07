<?php
/**
 * Plugin Name: MSW GitHub Coding Bridge
 * Description: Proponowany transport publicznych zadań kodowania przez istniejącą kolejkę MSW. Domyślnie HOLD.
 * Version: 0.1.0
 * Requires PHP: 8.3
 * Author: Mobilny Serwis Warszawa
 */
defined('ABSPATH') || exit;
require_once __DIR__.'/includes/class-msw-gcb-contract.php';
require_once __DIR__.'/includes/class-msw-gcb-options.php';
require_once __DIR__.'/includes/class-msw-gcb-bridge.php';
MSW_GCB_Bridge::boot();
