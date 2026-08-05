# Secure Streamlit access through Cloudflare Tunnel

This deployment keeps the Spray-Tec Business API in Azure and publishes only
the Mac Studio Streamlit dashboard through Cloudflare Tunnel. Streamlit remains
bound to `127.0.0.1:8501`; no router port forwarding or public origin IP is
required.

## Security boundary

- Cloudflare Access must protect the entire dashboard hostname.
- Prefer Microsoft Entra ID and an approved Spray-Tec group as the Access rule.
- Do not reuse this interactive-login hostname for the Custom GPT action API.
- Do not disable Streamlit XSRF or CORS protections.
- Store the remotely managed tunnel token only on the Mac Studio with mode 600.
- Never put the tunnel token in `.env`, shell history, screenshots, or Git.

Recommended routing:

```text
dashboard.spray-tec.com -> Cloudflare Access -> Cloudflare Tunnel -> http://127.0.0.1:8501
```

## Cloudflare setup

In Cloudflare Zero Trust:

1. Create a remotely managed tunnel named `spraytec-streamlit`.
2. Add a published application route for the dashboard hostname with service
   URL `http://127.0.0.1:8501`.
3. Create a self-hosted Access application covering the entire hostname.
4. Add an Allow policy for the approved Spray-Tec Entra group. Deny everyone
   else. Use short sessions and require MFA through Entra.
5. Copy only the tunnel token value to the Mac Studio. Do not run or save the
   generated token-bearing command.

## Mac Studio installation

The established checkout is:

```text
/Users/pheiniger/spray-tec/sharepoint_job_scanner_graph_api
```

Pull the repository and install `cloudflared`:

```bash
cd /Users/pheiniger/spray-tec/sharepoint_job_scanner_graph_api
git pull
brew install cloudflared
mkdir -p /Users/pheiniger/.cloudflared
```

Create the token file without putting the token in shell history:

```bash
umask 077
read -s "TUNNEL_TOKEN?Cloudflare tunnel token: "
printf '%s' "$TUNNEL_TOKEN" > /Users/pheiniger/.cloudflared/spraytec-streamlit.token
unset TUNNEL_TOKEN
chmod 600 /Users/pheiniger/.cloudflared/spraytec-streamlit.token
```

Install or replace the existing Streamlit launch agent and install the tunnel
launch agent:

```bash
cd /Users/pheiniger/spray-tec/sharepoint_job_scanner_graph_api
scripts/install_macos_streamlit_tunnel_services.sh
```

These are user LaunchAgents and start after `pheiniger` logs in. They restart
after unexpected exits. The installer intentionally replaces
`com.spraytec.streamlit` so the dashboard listens only on localhost.

## Verification

Verify the local dashboard before testing the public hostname:

```bash
curl -fsS http://127.0.0.1:8501/_stcore/health
launchctl print gui/$(id -u)/com.spraytec.streamlit
launchctl print gui/$(id -u)/com.spraytec.streamlit-tunnel
```

Then open the dashboard hostname from a device outside the office network. It
must show the Cloudflare Access login before any Streamlit content. Confirm that
an unapproved account is denied.

Logs are written under `/Users/pheiniger/spray-tec/logs`:

```bash
tail -n 100 /Users/pheiniger/spray-tec/logs/streamlit.err.log
tail -n 100 /Users/pheiniger/spray-tec/logs/streamlit-tunnel.err.log
```

## Rollback

Disable the tunnel without stopping the local dashboard:

```bash
launchctl bootout gui/$(id -u) /Users/pheiniger/Library/LaunchAgents/com.spraytec.streamlit-tunnel.plist
```

Disable both services:

```bash
launchctl bootout gui/$(id -u) /Users/pheiniger/Library/LaunchAgents/com.spraytec.streamlit-tunnel.plist
launchctl bootout gui/$(id -u) /Users/pheiniger/Library/LaunchAgents/com.spraytec.streamlit.plist
```

Deleting or disabling the Cloudflare published application route is the final
external cutoff. The Azure Business API is unaffected by these services.
