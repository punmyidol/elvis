import pandas as pd
import matplotlib.pyplot as plt

# 1. Read data from CSV file
data = pd.read_csv('data.csv')

# 2. Find data correlation
correlation_matrix = data.corr()

print("Correlation Matrix:")
print(correlation_matrix)

# 3. Map data on graphs
plt.figure(figsize=(10, 6))

# Plotting population vs GDP
plt.subplot(1, 2, 1)
plt.plot(data['Year'], data['Population'], label='Population')
plt.plot(data['Year'], data['GDP'], label='GDP')
plt.xlabel('Year')
plt.ylabel('Value')
plt.title('Population and GDP Over Time')
plt.legend()

# Plotting correlation heatmap
plt.subplot(1, 2, 2)
plt.imshow(correlation_matrix, cmap='coolwarm', interpolation='nearest')
plt.colorbar()
plt.xticks(range(len(data.columns)), data.columns)
plt.yticks(range(len(data.columns)), data.columns)
plt.title('Correlation Heatmap')

plt.tight_layout()

# Show the plots
plt.show()