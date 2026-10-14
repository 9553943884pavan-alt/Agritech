from pathlib import Path
import pandas as pd
import numpy as np

# Resolve directories relative to this script
SCRIPT_DIR = Path(__file__).resolve().parent
DATA_DIR = SCRIPT_DIR.parents[1] / 'data'
RAW_DIR = DATA_DIR / 'raw'
PROCESSED_DIR = DATA_DIR / 'processed'

print("Loading datasets...")
df1 = pd.read_csv(RAW_DIR / 'Crop_recommendation.csv')
df2 = pd.read_csv(RAW_DIR / 'crop_production.csv')

# Create a reverse mapping dictionary (Dataset 2 Official Name -> Dataset 1 Standard Name)
# Note: we use lowercase for matching
d2_to_d1 = {
    'gram': 'chickpea',
    'cotton(lint)': 'cotton',
    'rajmash kholar': 'kidneybeans',
    'moth': 'mothbeans',
    'moong(green gram)': 'mungbean',
    'other fresh fruits': 'muskmelon',
    'arhar/tur': 'pigeonpeas',
    'pome granet': 'pomegranate',
    'water melon': 'watermelon'
}

# Add the 13 perfect matches to the dictionary automatically
perfect_matches = ['apple', 'banana', 'blackgram', 'coconut', 'coffee', 'grapes', 
                   'jute', 'lentil', 'maize', 'mango', 'orange', 'papaya', 'rice']
for crop in perfect_matches:
    d2_to_d1[crop] = crop

print("Cleaning and mapping crop names...")
# Clean Dataset 2's crop column (lowercase and strip spaces)
df2['Crop_clean'] = df2['Crop'].astype(str).str.strip().str.lower()

# Map the cleaned names to our 22 standard names
df2['Standard_Crop'] = df2['Crop_clean'].map(d2_to_d1)

# Keep only the rows where the crop matched (dropping the ~170k useless rows)
df2_filtered = df2[df2['Standard_Crop'].notnull()].copy()

print("Handling missing values and calculating Yield...")
# Drop any rows where 'Area' or 'Production' data is missing (NaN)
df2_filtered = df2_filtered.dropna(subset=['Area', 'Production'])

# Remove any rows where Area is 0 to avoid division by zero errors
df2_filtered = df2_filtered[df2_filtered['Area'] > 0]

# Calculate the critical 'Yield' column (Tonnes per Hectare)
df2_filtered['Yield'] = df2_filtered['Production'] / df2_filtered['Area']

# Drop the temporary cleaning columns and reorder for neatness
df2_filtered = df2_filtered.drop(columns=['Crop_clean', 'Crop'])
# Rename Standard_Crop back to Crop for consistency
df2_filtered = df2_filtered.rename(columns={'Standard_Crop': 'Crop'})

# Save the pristine dataset!
PROCESSED_DIR.mkdir(parents=True, exist_ok=True)
output_path = PROCESSED_DIR / 'Cleaned_crop_production.csv'
df2_filtered.to_csv(output_path, index=False)

print("-" * 30)
print(f"[SUCCESS] Cleaned dataset saved as: {output_path}")
print(f"Total rows remaining: {len(df2_filtered)}")
print(f"Number of Unique Crops: {df2_filtered['Crop'].nunique()} (Target was 22)")
print("Preview of the new dataset:")
print(df2_filtered[['State_Name', 'Crop', 'Area', 'Production', 'Yield']].head())
