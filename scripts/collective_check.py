from datetime import timedelta
import os
import time

import torch
import torch.distributed as dist

rank = int(os.environ['NODE_RANK'])
torch.cuda.set_device(0)
dist.init_process_group('nccl', init_method='tcp://' + os.environ['COLLECTIVE_ADDR'],
                        rank=rank, world_size=4, timeout=timedelta(seconds=120))
for size in (256, 16384, 4 * 1024 * 1024):
    start = time.monotonic()
    for iteration in range(10):
        tensor = torch.full((size,), rank + 1, dtype=torch.float32, device='cuda')
        dist.all_reduce(tensor)
        torch.cuda.synchronize()
        if not torch.all(tensor == 10).item():
            raise RuntimeError(f'AllReduce 校验失败：rank={rank}, size={size}')
    print(f'AllReduce PASS rank={rank} elements={size} elapsed={time.monotonic()-start:.3f}s', flush=True)
source = torch.full((16384,), rank, dtype=torch.float32, device='cuda')
gathered = [torch.empty_like(source) for _ in range(4)]
dist.all_gather(gathered, source)
for peer, tensor in enumerate(gathered):
    if not torch.all(tensor == peer).item():
        raise RuntimeError(f'AllGather 校验失败：rank={rank}, peer={peer}')
print(f'AllGather PASS rank={rank}', flush=True)
dist.destroy_process_group()
