typedef unsigned char    byte;
typedef unsigned short   word;
typedef struct k7232_props k7232_props, *Pk7232_props;

struct k7232_props {
    byte bank;
    byte addr_hi;
    word addr;
    byte vstate_34;
    word addr_step; /* vstate 35/36, might be a bug where 34/35 are actually addr_step */
    byte vstate_1f;
    byte vstate_2f;
};

