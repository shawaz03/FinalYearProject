# Cluster troubleshooting

## kubectl hangs, then "Unable to connect to the server"

**Symptom.** Right after `k3d cluster create`, every `kubectl` command hangs for
~30 s and fails:

```
dial tcp 10.165.67.128:60452: connectex: A connection attempt failed ...
```

**Cause.** k3d writes `server: https://host.docker.internal:60452` into the
kubeconfig. On a machine with a VPN or an extra virtual network adapter,
`host.docker.internal` resolves to that adapter's address instead of the Docker
host, so kubectl dials the wrong machine. The cluster itself is fine.

**Check.** The port is listening on loopback even though the name is wrong:

```powershell
[System.Net.Dns]::GetHostAddresses("host.docker.internal")   # wrong address
docker ps --filter name=k3d-cloudpulse                        # serverlb is Up
```

**Fix.** Point the context at loopback instead:

```powershell
kubectl config set-cluster k3d-cloudpulse --server=https://127.0.0.1:<PORT>
```

`<PORT>` is the host port on `k3d-cloudpulse-serverlb` in `docker ps`. It is
assigned at creation time and changes if the cluster is recreated.

**Tip.** Always pass `--request-timeout=20s` to kubectl. Default behaviour is to
hang indefinitely, which wastes minutes before showing you the error.
