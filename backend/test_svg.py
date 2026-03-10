import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import io

fig, ax = plt.subplots()
ax.imshow(np.random.rand(10,10))
buf = io.BytesIO()
plt.savefig(buf, format='svg')
svg = buf.getvalue().decode('utf-8')
lines = [line for line in svg.split('\n') if 'image' in line or 'png' in line]
print(lines[:3])
