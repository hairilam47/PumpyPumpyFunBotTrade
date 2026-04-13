// tests/load/k6-load-test.js
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Counter, Trend, Rate } from 'k6/metrics';

// Custom metrics
const ordersSubmitted = new Counter('orders_submitted');
const ordersSucceeded = new Counter('orders_succeeded');
const ordersFailed = new Counter('orders_failed');
const orderLatency = new Trend('order_latency');
const errorRate = new Rate('error_rate');

export const options = {
    stages: [
        { duration: '30s', target: 10 },  // Ramp up to 10 users
        { duration: '1m', target: 50 },   // Ramp up to 50 users
        { duration: '2m', target: 100 },  // Ramp up to 100 users
        { duration: '30s', target: 0 },   // Ramp down to 0
    ],
    thresholds: {
        'order_latency': ['p(95)<1000'],  // 95% of orders under 1s
        'error_rate': ['rate<0.01'],       // Less than 1% error rate
    },
};

const BASE_URL = __ENV.BASE_URL || 'http://localhost:50051';
const TEST_MINTS = [
    'TestMint1111111111111111111111111111111111',
    'TestMint2222222222222222222222222222222222',
    'TestMint3333333333333333333333333333333333',
];

export default function() {
    // Random mint selection
    const mint = TEST_MINTS[Math.floor(Math.random() * TEST_MINTS.length)];
    
    // Submit order request
    const payload = JSON.stringify({
        token_mint: mint,
        order_type: 'MARKET',
        side: Math.random() > 0.5 ? 'BUY' : 'SELL',
        amount: Math.floor(Math.random() * 1000000) + 100000,
        slippage_bps: 100,
        strategy_name: 'load_test',
        metadata: {
            test_id: `${__VU}-${__ITER}`,
        }
    });
    
    const params = {
        headers: {
            'Content-Type': 'application/json',
        },
        timeout: '10s',
    };
    
    const start = Date.now();
    const response = http.post(`${BASE_URL}/v1/orders`, payload, params);
    const duration = Date.now() - start;
    
    ordersSubmitted.add(1);
    orderLatency.add(duration);
    
    const success = check(response, {
        'status is 200': (r) => r.status === 200,
        'response has order_id': (r) => r.json('order_id') !== '',
    });
    
    if (success) {
        ordersSucceeded.add(1);
        
        // Check order status after submission
        const orderId = response.json('order_id');
        sleep(0.5);
        
        const statusResponse = http.get(`${BASE_URL}/v1/orders/${orderId}`);
        check(statusResponse, {
            'status check returns 200': (r) => r.status === 200,
        });
    } else {
        ordersFailed.add(1);
        errorRate.add(1);
    }
    
    sleep(Math.random() * 2 + 1); // Random sleep between 1-3 seconds
}

export function setup() {
    console.log('Starting load test...');
    return { startTime: new Date().toISOString() };
}

export function teardown(data) {
    console.log(`Load test completed. Started at: ${data.startTime}`);
    console.log(`Total orders: ${ordersSubmitted.value}`);
    console.log(`Success rate: ${(ordersSucceeded.value / ordersSubmitted.value * 100).toFixed(2)}%`);
}