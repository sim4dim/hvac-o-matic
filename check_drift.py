import sys, json
states = json.load(sys.stdin)
for e in sorted(states, key=lambda x: x['entity_id']):
    eid = e['entity_id']
    if 'keenect_drift' in eid and eid != 'sensor.keenect_drift':
        a = e.get('attributes', {})
        print(eid + ': ' + e['state'])
        print('  overnight_drop=' + str(a.get('overnight_drop', '?')) +
              ' heat_loss_rate=' + str(a.get('heat_loss_rate', '?')) +
              ' sensor_step=' + str(a.get('sensor_step', '?')) +
              ' window=' + str(a.get('window_intervals', '?')))
