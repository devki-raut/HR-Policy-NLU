"""IT topic routing and reviewed interpretations of incident resolution notes.

Resolution summaries describe historical work by IT, never an instruction to
change shared infrastructure or proof of a new incident's cause.
"""
import re

TOPICS = {
    'it_network_outage': ('Network outage', ['Our office internet link is down', 'Neither WAN circuit is available', 'The site has no network connectivity'], 'Record the site, start time and whether other users are affected. IT can check the provider links and network equipment.'),
    'it_network_slowness': ('Slow or unstable network', ['The office internet is very slow', 'Our WAN link keeps flapping', 'Downloads are slow on Wi-Fi and LAN'], 'Record the site, time, affected applications and whether Wi-Fi, LAN or both are affected. IT can check utilisation, stability and provider faults.'),
    'it_remote_access': ('Citrix, VPN and ZTNA', ['Citrix access is denied', 'My VPN session keeps disconnecting', 'ZTNA will not connect'], 'Record the remote-access client, exact error and start time. Say whether the device or network changed recently. IT must verify access and device registration.'),
    'it_email_access': ('Email and mailbox', ['Outlook will not open my mailbox', 'Email attachments are loading slowly', 'My online archive mailbox is full'], 'Say whether the issue affects Outlook on the web, desktop or mobile, and provide the exact error. Access and archive changes need IT review.'),
    'it_meeting_equipment': ('Teams calls and meeting equipment', ['Teams calls keep dropping', 'Our meeting room HDMI cable is broken', 'The conference room USB C connector is stuck'], 'Record the affected meeting, room or device and the exact behaviour. IT can inspect the device settings, cables and network.'),
    'it_application_access': ('Business application', ['GEMS is unavailable', 'GKE will not open', 'Salesforce is not loading'], 'Record the application, exact error, start time and whether other applications work. Say whether access is direct or through Citrix.'),
    'it_storage_collaboration': ('OneDrive and Microsoft 365 groups', ['OneDrive files will not open', 'I need approved access to a colleague OneDrive', 'How do I request a Microsoft 365 group?'], 'Record the file or collaboration service and exact error. Access transfers and group creation need an approved IT request.'),
    'it_device_support': ('Computer and network hardware', ['My system is not booting', 'The SD WAN device is faulty', 'The LAN port is faulty'], 'Record the device, location and power or error indicators. Ask IT to inspect or replace faulty equipment; do not reconfigure shared equipment yourself.'),
    'it_security': ('Security connector and application restrictions', ['Zscaler connector shows a firewall error', 'An application is blocked by the security connector', 'I need an approved PDF exception'], 'Record the application and exact security message. Ask IT to review the restriction; do not disable antivirus, firewall or security checks.'),
    'it_account_access': ('AD and self-service account access', ['AD self service shows permission denied', 'My AD account is locked', 'My domain account is not synchronising'], 'Provide the exact sign-in error and affected account service, without passwords or MFA codes. IT can check authentication, permissions and policy synchronisation.'),
}


def topic(text):
    """Classify the described symptom, rather than an unreliable export label."""
    v = text.casefold()
    if re.search(r'citrix|vpn|ztna|\bspa\b|instasafe', v): return 'it_remote_access'
    if re.search(r'zscal|infected|antivirus|firewall|pdf.*(?:exception|exceptional)|adobe', v): return 'it_security'
    if re.search(r'ad\s*self\s*service|adselfservice|\bad account\b|domain account|\bad policy\b', v): return 'it_account_access'
    if re.search(r'one\s*drive|onedrive|microsoft\s*365\s*group|m365\s*group', v): return 'it_storage_collaboration'
    if re.search(r'teams|team calls|hdmi|cable cubby|c-type|usb|meeting.*(?:audio|camera|microphone)', v): return 'it_meeting_equipment'
    if re.search(r'not boot|booting|faulty|not powering|switch.*(?:communicat|fault)|sd\s*wan.*(?:fault|power)|port.*(?:fault|broken)', v): return 'it_device_support'
    # Working Outlook is context for a GEMS failure, not an email failure.
    if re.search(r'\bgems\b|\bgke\b|intellihub|salesforce', v) and re.search(r'outlook\s+(?:(?:is|was|still)\s+)?(?:works|worked|working|available)', v): return 'it_application_access'
    if re.search(r'outlook|outlok|outllook|netcore|mail|archive', v) and not re.search(r'network|internet|wi[ -]?fi|\blink\b|\blinks\b', v): return 'it_email_access'
    if re.search(r'network|internet|wi[ -]?fi|\blan\b|\bwan\b|\blinks?\b|airtel|sify|tikona|adtech|bsnl|media down', v):
        return 'it_network_slowness' if re.search(r'slow|flap|fluctuat|intermittent|performance|dropp', v) else 'it_network_outage'
    return 'it_application_access'

# key -> canonical question, training paraphrases, held-out paraphrases, summary.
# Test paraphrases are kept out of generated NLU and retrieval candidates.
RESOLUTIONS = {
 'dns': ('Why can GEMS fail while Outlook still works?', ['GEMS unavailable while Outlook worked', 'GEMS not working but Outlook works', 'GEMS fails directly although email still works'], ['Outlook remains accessible whereas GEMS cannot be opened', 'Email is fine; the GEMS intranet is inaccessible'], 'In incident 346506, DNS configuration was missing on the broadband link. IT corrected the DNS issue.'),
 'geo': ('What happened in the Citrix access denied incident?', ['Citrix access denied after an ISP location mismatch', 'Citrix is detecting the wrong country', 'Citrix sign in blocked by geolocation checks'], ['Citrix refuses access because the ISP makes me appear overseas', 'My Citrix connection is denied and my location appears as Switzerland'], 'In incident 344498, ISP routing made the user appear to be in Switzerland and a geolocation posture check blocked access. The report says access worked after switching networks. IT should verify the routing and security check; do not bypass it.'),
 'mac': ('Can a changed device MAC address affect Citrix access?', ['Citrix stopped working after I changed my laptop', 'How does MAC binding affect Citrix?', 'The remote access client needs my new device registered'], ['I replaced my PC and now Citrix refuses to connect', 'Remote applications stopped opening after a device change'], 'The report records MAC-bound access for customer/outsider users. After a device change, IT must review and update the registered MAC address. The recorded request asks for the user ID, device MAC address and an error screenshot. This rule must not be assumed to apply to every employee.'),
 'fiber': ('What caused the reported provider fibre-cut outages?', ['The ISP confirmed a fiber cut on our link', 'Our provider reported an underground fibre break', 'What did IT do for a fibre-cut outage?'], ['The circuit failed after a confirmed optical cable break', 'A severed fibre cable has taken the provider connection offline'], 'The report records provider fibre cuts; connectivity was restored after the provider repaired the affected fibre. IT should track the provider incident and available alternate connectivity.'),
 'alignment': ('What fixed the reported wireless provider alignment problems?', ['Our ISP confirmed radio alignment trouble', 'Tikona reports an alignment issue', 'A provider RF alignment fault is affecting our link'], ['The provider says the radio alignment needs correcting', 'The wireless circuit is unstable because of antenna alignment'], 'Recorded incidents were restored after the provider corrected radio/backend alignment issues. IT must coordinate provider checks rather than assume every link outage is an alignment fault.'),
 'utilisation': ('What happened when network links were fully utilised?', ['IT confirmed that our internet circuit is fully utilised', 'The office link has saturated bandwidth', 'The provider link capacity is exhausted'], ['Traffic has consumed the entire WAN capacity', 'Network monitoring confirms sustained bandwidth saturation'], 'The report records high link utilisation causing slow applications or calls. IT investigated utilisation and raised capacity/upgrade feasibility where needed. Upgrade requests and restoration status varied by incident.'),
 'load_balance': ('What fixed the recorded load-balancing problems?', ['IT found a load balancing fault on the WAN', 'One ISP link is not taking traffic', 'A network load balancing policy is causing slowness'], ['Traffic is not being distributed across the WAN circuits', 'The network team identified an incorrect load balancing configuration'], 'In recorded incidents, IT changed load-balancing configuration or policy to restore traffic and application performance. These are network-team changes, not settings to change yourself.'),
 'bts': ('What caused the reported base-station outages?', ['The provider reported a BTS outage', 'Our ISP base station has a power fault', 'The link is down due to a tower fault'], ['The circuit failure was traced to the provider base station', 'Provider tower equipment is offline'], 'The report records provider-side outages, including base-station/tower faults and power fluctuations; some notes use FDN/FTN labels without further explanation. IT coordinated restoration with the provider.'),
 'device_stuck': ('What fixed a stuck Lavelle network device?', ['The Lavelle box is stuck', 'The Lavelle device hangs and applications stop responding', 'Our network appliance is unresponsive'], ['The Lavelle appliance has stopped responding to traffic', 'The network team found the Lavelle box hung'], 'Recorded incidents were restored after IT restarted an unresponsive Lavelle device and, in some cases, restarted applications or performed link testing. Shared infrastructure restarts must be handled by IT.'),
 'cloud_station': ('Why was the Lavelle Cloud Station unreachable?', ['Lavelle Cloud Station is offline', 'The Lavelle cloud server is unavailable', 'Cloud station cannot be reached'], ['The Lavelle management station cannot reach its cloud server', 'Lavelle cloud station went offline after a server error'], 'Recorded Lavelle Cloud Station outages were attributed to cloud/server faults. The report records server-side restoration.'),
 'port': ('What happened when a network LAN port was faulty?', ['The SD WAN LAN port has failed', 'A network device LAN port is faulty', 'The provider link is up but the LAN port is broken'], ['The network team diagnosed a damaged LAN connector on the device', 'A failed LAN port is stopping local connectivity'], 'In incident 348383, IT identified a faulty LAN port, raised an RMA and temporarily reassigned a WAN port as a LAN port. Port changes and replacement must be handled by IT.'),
 'adapter': ('What fixed the reported faulty power adapter?', ['The network device power adapter is faulty', 'Our WAN device adapter has failed', 'The link stopped because its adapter failed'], ['The network appliance has lost power through a defective adaptor', 'A defective power supply is keeping the site link offline'], 'A recorded site outage was restored after replacing a faulty adapter. IT should inspect the hardware and arrange an approved replacement.'),
 'sdwan': ('What happened when the SD WAN device was faulty?', ['Our SD WAN appliance is faulty', 'The SDWAN device will not power on', 'A standby SD WAN device is needed'], ['The branch SDWAN unit has failed and needs replacement', 'The SD WAN box is dead at the office'], 'Incident 344882 records that a standby SDWAN device was arranged and the site was restored. IT should inspect the device and arrange replacement as needed.'),
 'ad_policy': ('What did the report record about AD policy synchronisation?', ['The domain policy is not syncing', 'IT identified an AD policy synchronisation problem', 'AD policy sync is failing'], ['Active Directory policy changes are failing to synchronise', 'The domain policy has stopped syncing to the machine'], 'The report records AD policy issues being addressed and synchronisation working afterward, but it does not document the technical steps. IT must investigate the actual error.'),
 'ad_auth': ('What was recorded for an AD authentication failure?', ['AD authentication has failed', 'The domain authentication service rejects my login', 'IT found an AD authentication issue'], ['My directory authentication is failing', 'The domain sign in is being rejected by AD'], 'The report says an AD authentication issue was resolved, without documenting the technical fix. Ask IT to check the sign-in error; do not infer a password reset or permission change from that note.'),
 'mailbox_missing': ('What if a work mailbox has not been created?', ['IT says no mailbox exists for my work account', 'My mailbox was never created', 'The work email account has no provisioned mailbox'], ['My user ID exists but a mailbox has not been provisioned', 'The helpdesk found that my work mailbox does not exist'], 'The report records a mailbox that had not been created and directs the requester to check with HR. HR and IT need to verify the provisioning request; the bot cannot create the mailbox.'),
 'archive': ('What was recorded about enabling mailbox archive?', ['How do I request email archive enablement?', 'IT needs to enable my mailbox archive', 'My work email needs online archive enabled'], ['I need the helpdesk to activate mailbox archiving', 'Can IT enable the archive feature on my mailbox?'], 'A recorded incident says archive was enabled. The report does not provide end-user configuration steps; ask IT to check archive provisioning and capacity.'),
 'onedrive_access': ('How was OneDrive and mailbox access handled?', ['I need approved access to a colleague OneDrive and mailbox', 'IT must grant access to a former colleague OneDrive', 'How can I request a OneDrive access transfer?'], ['How do I obtain approved access to another employee files in OneDrive?', 'A colleague OneDrive and mailbox need an authorised access handover'], 'A recorded request says IT provided OneDrive and full mailbox access to specified authorised accounts. New access must be reviewed by IT; historical approval does not grant access to other users.'),
 'm365_group': ('How can I request a Microsoft 365 group?', ['Microsoft 365 group creation is restricted', 'I cannot create a new M365 group', 'Who can create an Office 365 group?'], ['I need a Microsoft 365 group but users cannot create one', 'What details should I give IT for a new M365 collaboration group?'], 'The report says Microsoft 365 group creation was restricted for users. Raise an IT request explaining the exact purpose/use of the group; the IT team creates the group.'),
 'dc_outage': ('What if many business applications fail at all locations?', ['GEMS and other applications are down across all sites', 'All branches lost access to business applications', 'The data centre outage affects all applications'], ['Every location has lost several critical business systems at once', 'GEMS IntelliHub and booking systems are unavailable company wide'], 'The report records a data-centre issue that affected applications across locations. IT should check service-wide incident status rather than assume a local access fault.'),
 'new_access': ('How do I request a new Citrix installation or entitlement?', ['I need new Citrix access', 'Please enable Citrix for a new user', 'How do I request a new remote access entitlement?'], ['A new joiner needs Citrix provisioned', 'What process is recorded for a new remote application access requirement?'], 'A recorded new Citrix requirement was redirected to a service request (SR). Submit the requested access and business requirement for IT review.'),
 'local_it': ('Who should handle an incident outside the assigned team scope?', ['The assigned support team says this is outside their scope', 'My AD self service permission error was referred to local IT', 'Who handles the issue if this team cannot support it?'], ['The incident was closed because a different IT team owns the service', 'The assigned analyst asked me to contact the concerned local support team'], 'The report directs out-of-scope issues to the concerned local IT or support team. Closing the incident for scope does not establish that the underlying problem was fixed.'),
 'no_logs': ('What if Citrix login fails but support finds no logs?', ['Citrix login fails and IT finds no user logs', 'Remote access failed but no client logs were generated', 'IT cannot find logs for my Citrix login attempt'], ['A failed Citrix sign in has no corresponding user logs', 'No authentication logs appear for my attempted remote access'], 'The report asks IT to check the user machine, local log generation and network connectivity when no logs are found. The recorded notes say the available evidence did not establish an access-permission issue.'),
 'traffic_failover': ('What fixed calls affected by provider-link drops?', ['Calls drop when the Tikona circuit drops', 'IT needs to move video traffic to another provider', 'Video calls fail on one ISP link'], ['Conferencing breaks when the primary provider circuit loses packets', 'The voice fault follows one unstable WAN provider'], 'Recorded call issues improved after IT moved voice/video traffic from an unstable provider link to another available provider. IT should first verify link quality and capacity.'),
 'upgrade': ('What was recorded about network device and link upgrades?', ['IT plans to upgrade both links and the Lavelle box', 'Both WAN links need a capacity upgrade', 'The branch needs a network upgrade feasibility check'], ['The network team proposes circuit and appliance upgrades', 'How were the recorded branch link upgrade requests handled?'], 'The report includes both completed link/device upgrades and pending upgrade-feasibility requests. IT must confirm the current approval and completion status; the bot cannot promise an upgrade date.'),
 'power': ('What if both provider links go down at the same time?', ['Both WAN circuits dropped and recovered together', 'Both providers went offline at exactly the same time', 'Could site power explain simultaneous link loss?'], ['Two ISP circuits failed and returned at the same instant', 'Both internet links recover together after a site power interruption'], 'One recorded incident noted both links failing and recovering together and asked for a site power check. This is a possible investigation lead, not proof of a power fault.'),
 'poe': ('What fixed the recorded hung PoE device?', ['The provider PoE equipment is hung', 'IT found that the PoE injector was stuck', 'Our provider PoE device is unresponsive'], ['The network engineer diagnosed a hung power over Ethernet unit', 'The provider connection stopped after its PoE equipment froze'], 'A recorded Tikona outage was restored after the hung PoE equipment was restarted. IT/provider staff should inspect and handle the equipment.'),
 'sessions': ('What fixed the link that was not taking load?', ['The provider link recovered after IT reset sessions', 'Our circuit is up but not accepting application traffic', 'A link session problem is preventing applications from working'], ['The network team suspects a stale provider session', 'The circuit carries no load until its sessions are reset'], 'A recorded incident says a provider link was not taking load and began working after a session reset. IT should verify the fault and perform any required network changes.'),
 'salesforce': ('What was recorded for Salesforce traffic routing?', ['Salesforce needs to use the primary WAN link', 'IT found Salesforce routing trouble', 'Salesforce worked after changing its provider route'], ['Salesforce is failing on the secondary circuit but works on the primary', 'The network team moved Salesforce traffic to the primary connection'], 'A recorded incident says Salesforce worked after IT dedicated its traffic to the primary link. IT must verify application routing before applying a similar change.'),
}


def resolution_key(solution):
    v = solution.casefold()
    if not v.strip(): return 'missing'
    if re.search(r'no reply|no update|follow.up', v): return 'closure_only'
    if 'outside' in v and 'mac' not in v or 'not fall under' in v or 'not come under' in v or 'concerned local' in v: return 'local_it'
    if 'dns' in v: return 'dns'
    if 'switzerland' in v or 'geolocation' in v: return 'geo'
    if 'mac' in v and re.search(r'bind|address|bound', v): return 'mac'
    if 'no logs' in v: return 'no_logs'
    if 'mailbox not created' in v: return 'mailbox_missing'
    if 'group creation' in v: return 'm365_group'
    if 'onedrive and full mailbox' in v: return 'onedrive_access'
    if 'archive has been enabled' in v: return 'archive'
    if 'new requirement' in v: return 'new_access'
    if 'ad policy' in v: return 'ad_policy'
    if 'ad authentication' in v: return 'ad_auth'
    if 'issue at dc' in v: return 'dc_outage'
    if 'lan port has gone faulty' in v: return 'port'
    if 'adaptor was faulty' in v: return 'adapter'
    if 'standby sdwan' in v or 'device has been replaced' in v: return 'sdwan'
    if 'cloud' in v or 'lavelle server was down' in v: return 'cloud_station'
    if 'power issue' in v: return 'power'
    if 'poe got hung' in v: return 'poe'
    if 'session reset' in v: return 'sessions'
    if 'salesforce' in v: return 'salesforce'
    if 'shifted' in v and re.search(r'voice|vc traffic', v): return 'traffic_failover'
    if 'load balancing' in v: return 'load_balance'
    if re.search(r'lavelle.*(?:stuck|reboot)|reboot.*lavelle', v): return 'device_stuck'
    if 'upgraded' in v and 'links' in v: return 'upgrade'
    if re.search(r'utiliz|usage is high|overutil', v): return 'utilisation'
    if re.search(r'fiber|fibre|ofc major cable', v): return 'fiber'
    if re.search(r'align|frequency', v): return 'alignment'
    if re.search(r'bts|base station|tower|fdn|ftn', v): return 'bts'
    if re.search(r'feasib|upgrad', v): return 'upgrade'
    if re.search(r'working (?:fine|normal)|no issue|under observation|loading is normal|stable', v): return 'observation'
    return 'unspecified'


def resolution_summary(key):
    if key in RESOLUTIONS: return RESOLUTIONS[key][3]
    return {
        'missing': 'The report contains no solution for this incident. A resolution cannot be inferred from its status.',
        'closure_only': 'The note records follow-ups or administrative closure without a technical fix. It does not establish how the problem was resolved.',
        'observation': 'The note records normal operation or observation, without establishing a reproducible technical fix.',
        'unspecified': 'The report records an update or closure but does not provide enough detail for a reproducible technical fix.',
    }[key]
